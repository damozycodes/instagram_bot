import time
import pickle
import getpass
import random
import requests
import traceback
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from webdriver_manager.chrome import ChromeDriverManager


COOKIE_FILE = "tiktok_cookies.pkl"
MAX_SCROLLS = 100
COMMENT_RETRY_ATTEMPTS = 1        # attempts to open comment pane
SCROLL_RETRY_ATTEMPTS = 3         # attempts to perform a scroll if it fails
SKIP_PROB = 0.15                  # probability to skip the like (to appear natural)
LONG_PAUSE_PROB = 0.05            # occasional longer pause chance
LONG_PAUSE_MIN = 5
LONG_PAUSE_MAX = 12


def human_sleep(min_s=0.4, max_s=1.4):
    time.sleep(random.uniform(min_s, max_s))


def human_scroll_element(driver, element, total_px=600, step_px=120, min_pause=0.25, max_pause=0.9):
    """
    Scroll an element by small steps to simulate a human reading/scrolling.
    Falls back to window scroll if element scroll fails.
    """
    try:
        scrolled = 0
        while scrolled < total_px:
            step = min(step_px, total_px - scrolled)
            driver.execute_script("arguments[0].scrollTop += arguments[1];", element, step)
            scrolled += step
            time.sleep(random.uniform(min_pause, max_pause))
    except Exception:
        # fallback: page scroll
        pos = 0
        while pos < total_px:
            step = min(step_px, total_px - pos)
            driver.execute_script("window.scrollBy(0, arguments[0]);", step)
            pos += step
            time.sleep(random.uniform(min_pause, max_pause))


def human_move_and_click(driver, element):
    """
    Move the mouse to the element with small randomized offsets then click.
    Uses ActionChains to make clicks look more human.
    """
    try:
        actions = ActionChains(driver)
        # small random offset
        offset_x = random.randint(-6, 6)
        offset_y = random.randint(-6, 6)
        actions.move_to_element_with_offset(element, offset_x, offset_y).pause(random.uniform(0.05, 0.25)).click().perform()
    except Exception:
        # fallback to JS click
        try:
            driver.execute_script("arguments[0].click();", element)
        except Exception:
            pass


def validate_url(url):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/117.0.0.0 Safari/537.36"
        }
        response = requests.get(url, headers=headers, allow_redirects=True, timeout=10)
        # Consider valid if NOT 404/410
        if response.status_code in [404, 410]:
            return False
        return True
    except Exception:
        return False


def read_video_links(file_path):
    try:
        with open(file_path, 'r') as file:
            links = [line.strip() for line in file if line.strip()]
            links = list(dict.fromkeys(links))  # preserve order, remove duplicates
            print(f"Total unique links found: {len(links)}")
        valid_links = [link for link in links if validate_url(link)]
        if len(valid_links) < len(links):
            print(f"Warning: {len(links) - len(valid_links)} invalid or inaccessible links skipped.")
        print("Loaded links for processing")
        return valid_links
    except Exception as e:
        print(f"Error reading video links from {file_path}: {e}")
        return []


def save_cookies(driver, path=COOKIE_FILE):
    try:
        with open(path, "wb") as f:
            pickle.dump(driver.get_cookies(), f)
        print(f"Saved cookies to {path}")
    except Exception as e:
        print(f"Error saving cookies: {e}")


def load_cookies(driver, url="https://www.tiktok.com", path=COOKIE_FILE):
    try:
        with open(path, "rb") as f:
            cookies = pickle.load(f)
        driver.get(url)
        time.sleep(2)
        for c in cookies:
            # remove problematic keys
            if "sameSite" in c:
                c.pop("sameSite")
            try:
                driver.add_cookie(c)
            except Exception:
                pass
        driver.refresh()
        time.sleep(2)
        print(f"Loaded cookies from {path}")
        return True
    except FileNotFoundError:
        print("No cookies file found. Manual login required.")
        return False
    except Exception as e:
        print(f"Error loading cookies: {e}")
        return False


def check_for_captcha(driver):
    try:
        captcha = driver.find_elements(
            By.CSS_SELECTOR,
            "iframe[src*='captcha'], div[id*='captcha'], div[class*='captcha'], div[role='dialog']"
        )
        if captcha:
            print("CAPTCHA detected. Solve it manually in the browser. Waiting...")
            while True:
                captcha = driver.find_elements(
                    By.CSS_SELECTOR,
                    "iframe[src*='captcha'], div[id*='captcha'], div[class*='captcha'], div[role='dialog']"
                )
                avatar = driver.find_elements(By.CSS_SELECTOR, "img[class*='ImgAvatar']")
                if avatar:
                    print("Avatar detected → logged in. Resuming.")
                    return True
                if not captcha:
                    print("CAPTCHA seems gone. Resuming.")
                    return True
                time.sleep(2)
        return False
    except Exception as e:
        print(f"Error checking for CAPTCHA: {e}")
        return False


def check_login_status(driver, timeout=8):
    """
    Check TikTok login state:
      - Login button present -> 'not_logged_in'
      - Avatar/profile present -> 'logged_in'
      - Neither -> 'unknown'
    """
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((
                By.CSS_SELECTOR,
                "button[data-e2e='top-login-button'], img[class*='ImgAvatar'], div[class*='DivAvatarContainer'] img"
            ))
        )
    except Exception:
        return "unknown"

    # Login button visible?
    try:
        login_btn = driver.find_element(By.CSS_SELECTOR, "button[data-e2e='top-login-button']")
        if login_btn.is_displayed():
            return "not_logged_in"
    except Exception:
        pass

    # Avatar visible? (logged in)
    try:
        avatar = driver.find_element(By.CSS_SELECTOR, "img[class*='ImgAvatar'], div[class*='DivAvatarContainer'] img")
        if avatar.is_displayed():
            return "logged_in"
    except Exception:
        pass

    return "unknown"


def wait_for_manual_login(driver, poll_interval=2, timeout=180):
    """
    Block until avatar/profile appears or timeout.
    """
    start = time.time()
    print("Please log in manually in the opened Chrome window...")
    while True:
        status = check_login_status(driver, timeout=4)
        if status == "logged_in":
            print("Detected logged-in state (avatar/profile).")
            return True
        if time.time() - start > timeout:
            print("Timeout waiting for manual login.")
            return False
        if status == "not_logged_in":
            print("Login button still visible. Please complete login.")
        else:
            print("Still waiting for login…")
        time.sleep(poll_interval)


def get_driver_with_profile():
    options = Options()
    user = getpass.getuser()
    custom_user_data_dir = f"C:/Users/{user}/AppData/Local/Google/Chrome/TikTokBotProfile"
    options.add_argument(f"--user-data-dir={custom_user_data_dir}")
    options.add_experimental_option("detach", True)
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--log-level=3")
    options.add_argument("--disable-logging")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    return driver



def scroll_and_like_comments(driver, comments_section, max_scrolls=MAX_SCROLLS):
    """
    Scroll the comments section and like comments as they come into view,
    using randomness and skipping logic to avoid detection.
    Stops early if no new comments are loaded for several scrolls.
    """
    seen_comments = set()
    likes_count = 0
    attempts = 0
    prev_seen = 0
    stagnant_loops = 0   # counter for consecutive loops with no new comments
    MAX_STAGNANT_LOOPS = 5  # stop if no new comments after 5 loops

    for i in range(max_scrolls):
        attempts += 1

        # occasional longer pause to mimic human behaviour
        if random.random() < LONG_PAUSE_PROB:
            pause = random.uniform(LONG_PAUSE_MIN, LONG_PAUSE_MAX)
            print(f" Taking a longer pause for {pause:.1f}s (natural behaviour).")
            time.sleep(pause)

        # try to scroll; allow small number of retries
        scrolled = False
        for s_try in range(SCROLL_RETRY_ATTEMPTS):
            try:
                human_scroll_element(driver, comments_section, total_px=random.randint(400, 1000), step_px=120)
                scrolled = True
                break
            except Exception as e:
                print(f"Scroll attempt {s_try+1}/{SCROLL_RETRY_ATTEMPTS} failed: {e}")
                human_sleep(0.3, 0.8)
        if not scrolled:
            print("Unable to scroll comments further; breaking out.")
            break

        human_sleep(0.6, 1.6)

        # find comment blocks currently in DOM
        from selenium.common.exceptions import StaleElementReferenceException
# New point of modification, tiktok most  likely changes the comment DOM every time
        try:
            comments_section = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((
                    By.XPATH,
                    "//div[contains(@class,'DivCommentListContainer')]"
                ))
            )
        except Exception:
            print("Comment list container not found (re-render). Retrying...")
            continue

        try:
            comment_items = comments_section.find_elements(
                By.XPATH,
                ".//div[contains(@class,'DivCommentObjectWrapper')]"
            )
        except StaleElementReferenceException:
            print("Comments went stale. Re-looping...")
            continue

        

        if not comment_items:
            print("No comment elements found after scroll.")
            if i > 10 and attempts > 3:
                print("No comments found after several attempts; stopping.")
                break
            continue

        # track before processing this batch
        before_count = len(seen_comments)

        for comment in comment_items:
            try:
                # create identifier
                comment_id = None
                try:
                    comment_id = comment.get_attribute("data-id") or comment.get_attribute("data-comment-id")
                except Exception:
                    comment_id = None

                comment_text = (comment.text or "").strip()
                unique_key = comment_id if comment_id else (comment_text[:180] if comment_text else None)

                if not unique_key or unique_key in seen_comments:
                    continue

                seen_comments.add(unique_key)

                # scroll into view
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", comment)
                    human_sleep(0.25, 0.9)
                except Exception:
                    human_sleep(0.2, 0.5)

                # skip some comments
                if random.random() < SKIP_PROB:
                    print(f"Skipping comment (natural skip) — total seen: {len(seen_comments)}")
                    human_sleep(0.2, 0.8)
                    continue

                # find like button
                like_button = None
                try:
                    like_button = comment.find_element(
                        By.CSS_SELECTOR,
                        "[role='button'][aria-label*='like'], [aria-label*='like'][class*='like'], div[data-e2e*='comment-like']"
                    )
                except Exception:
                    like_button = None

                if not like_button:
                    print("Like button not located for this comment.")
                    human_sleep(0.2, 0.6)
                    continue

                # check already liked via aria-pressed
                aria_pressed = like_button.get_attribute("aria-pressed")
                already_liked = (aria_pressed is not None and aria_pressed.lower() == "true")

                if already_liked:
                    print(" Already liked (skipping).")
                    human_sleep(0.15, 0.5)
                    continue

               # click like
                try:
                    human_move_and_click(driver, like_button)
                    human_sleep(0.6, 1.2)

                    # re-check state after clicking using aria-pressed
                    aria_pressed = like_button.get_attribute("aria-pressed")
                    now_liked = (aria_pressed is not None and aria_pressed.lower() == "true")

                    if now_liked:
                        likes_count += 1
                        print(f"Liked comment #{likes_count} (seen: {len(seen_comments)})")
                    else:
                        print("First click didn’t stick, retrying once...")
                        human_move_and_click(driver, like_button)
                        human_sleep(0.6, 1.2)

                        aria_pressed = like_button.get_attribute("aria-pressed")
                        now_liked = (aria_pressed is not None and aria_pressed.lower() == "true")

                        if now_liked:
                            likes_count += 1
                            print(f"Liked on retry (#{likes_count})")
                        else:
                            print("Still not liked after retry, skipping.")
                except Exception as e:
                    print(f"Error clicking like button: {e}")
                    human_sleep(0.3, 0.8)

            except Exception as e:
                print(f"Error processing a comment: {e}")
                continue

        # plateau detection
        if len(seen_comments) == before_count:
            stagnant_loops += 1
            print(f"No new comments loaded this round. It is a Stagnant loops: {stagnant_loops}/{MAX_STAGNANT_LOOPS}")
            if stagnant_loops >= MAX_STAGNANT_LOOPS:
                print("Reached end of the comment (no new comments for several loops). Stopping early and proceeding to the next link if available.")
                break
        else:
            stagnant_loops = 0  # reset when new comments are found

        if random.random() < 0.08:  # scroll up sometimes
            try:
                driver.execute_script("arguments[0].scrollTop -= arguments[1];", comments_section, random.randint(80, 250))
                human_sleep(0.4, 1.0)
            except Exception:
                pass

        if i % 10 == 0:
            print(f"Scroll loop {i+1}/{max_scrolls} — liked: {likes_count} — seen_comments: {len(seen_comments)}")

    print(f"Finished scroll-and-like: total liked = {likes_count}, total seen comments = {len(seen_comments)}")
    return likes_count


def open_comments_panel(driver, link, selector, attempts=COMMENT_RETRY_ATTEMPTS):
    """
    Tries to open the comments panel for a post multiple times.
    Returns the comments_section WebElement or None on failure.
    """
    for attempt in range(1, attempts + 1):
        try:
            driver.get(link)
            human_sleep(1.2, 2.5)

            # wait for page to be interactive
            try:
                WebDriverWait(driver, 10).until(lambda d: d.execute_script("return document.readyState") in ("interactive", "complete"))
            except Exception:
                pass

            # find and click the comments icon/button
            comment_click = WebDriverWait(driver, 8).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
            )
            try:
                comment_click.click()
            except Exception:
                # fallback: JS click
                driver.execute_script("arguments[0].click();", comment_click)

            # wait for comments container to appear (it may be separate from the button)
            comments_section = WebDriverWait(driver, 12).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div[class*='DivCommentContainer'], div[data-e2e*='comment-list'], div[class*='DivCommentListContainer']"))
            )
            if comments_section:
                print ("Comment container found")
            human_sleep(0.8, 1.6)
            return comments_section

        except Exception as e:
            print(f"Attempt {attempt}/{attempts} to open comments failed: {e}")
            human_sleep(1.0 + attempt * 0.4, 1.6 + attempt * 0.6)
            continue

    print("All attempts to open the comments panel failed.")
    return None


def like_comments(video_links):
    try:
        driver = get_driver_with_profile()
        print("Connected to Chrome with persistent profile.")

        # load cookies or wait for manual login
        if not load_cookies(driver):
            print("Please log in manually in the opened Chrome window.")
            if wait_for_manual_login(driver):
                save_cookies(driver)
            else:
                print("Continuing without saved cookies (you may need to log in manually).")
        else:
            status = check_login_status(driver)
            if status == "logged_in":
                print("Bypassing login — already logged in.")
            elif status == "not_logged_in":
                print("Detected login button (not logged in). Waiting for manual login.")
                if wait_for_manual_login(driver):
                    save_cookies(driver)
    except Exception as e:
        print(f"Error starting Chrome with profile: {e}")
        return

    time.sleep(3)
    processed_links = 0

    try:
        for link in video_links:
            try:
                print(f"Processing link: {link}")
               
                if check_for_captcha(driver):
                    human_sleep(1.0, 2.0)

                # # comment icon/button selector (broad set)
                # selector = (
                #     "div.css-x4x1c7-DivCommentContainer, div.css-1adgkz8-DivCommentContainer, "
                #     "div[class*='DivCommentListContainer'], div[data-e2e*='comment-list'],"
                #     "button span[data-e2e='comment-icon'], button[aria-label*='comments'], a[href*='#comments']"
                # )
                
                # comment icon/button selector (broad set)
                selector = (
                    "div.css-x4x1c7-DivCommentContainer, "
                    "div.css-1adgkz8-DivCommentContainer, "
                    "div[class*='DivCommentListContainer'], "
                    "div[data-e2e*='comment-list'], "
                    "button span[data-e2e='comment-icon'], "
                    "button:has(span[data-e2e='comment-icon']), "
                    "button[aria-label*='comments'], "
                    "a[href*='#comments']"
                )


                comments_section = open_comments_panel(driver, link, selector, attempts=COMMENT_RETRY_ATTEMPTS)
                if not comments_section:
                    print(f"Skipping {link}: couldn't open comments after retries.")
                    continue

                print("Comments panel opened. Starting scroll-and-like routine...")
                liked = scroll_and_like_comments(driver, comments_section, max_scrolls=MAX_SCROLLS)
                print(f"Done with this post: liked {liked} comments on {link}")
                processed_links += 1

                # small delay between posts
                human_sleep(2.0, 4.0)

            except Exception as e:
                print(f"Unexpected error while processing {link}: {e}")
                print(traceback.format_exc())
                human_sleep(2.0, 4.0)
                continue

        print(f"\nCompleted processing {processed_links} out of {len(video_links)} links.")
    finally:
        try:
            pass
            # driver.quit()
        except Exception:
            pass



if __name__ == "__main__":
    video_links_file = "video_links.txt"
    video_links = read_video_links(video_links_file)
    if video_links:
        like_comments(video_links)
    else:
        print("No valid links provided. Please add links to video_links.txt.")



# # == Links, return back to video_links.txt
# https://www.tiktok.com/t/ZTMq54aqA/

# https://www.tiktok.com/t/ZTMqa1LmV/

# https://www.tiktok.com/t/ZTMq5EbeC/

# https://www.tiktok.com/t/ZTMq5sg7F/

# https://www.tiktok.com/t/ZTMq5gMbF/

# https://www.tiktok.com/t/ZTMqa1d2b/

# https://www.tiktok.com/t/ZTMq5g98r/

# https://www.tiktok.com/t/ZTMaXVsdL/