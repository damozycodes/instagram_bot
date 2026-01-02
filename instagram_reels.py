import re
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



COOKIE_FILE = "instagram_cookies.pkl"
MAX_SCROLLS = 8
COMMENT_RETRY_ATTEMPTS = 1        # attempts to open comment pane
SCROLL_RETRY_ATTEMPTS = 1         # attempts to perform a scroll if it fails
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

def load_cookies(driver, url="https://www.instagram.com", path=COOKIE_FILE):
    driver.get(url)
    try:
        with open(path, "rb") as f:
            cookies = pickle.load(f)
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

def check_login_status(driver, timeout=8):
    """
    Check Instagram login state:
      - Login button present -> 'not_logged_in'
      - Avatar/profile present -> 'logged_in'
      - Neither -> 'unknown'
    """

    # Login button visible?
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((
                By.ID,
                "loginForm"
            ))
        )

        login_btn = driver.find_element(By.ID, "loginForm")

        if login_btn.is_displayed():
            return "not_logged_in"    
    except Exception:
        return "logged_in"


def wait_for_manual_login(driver, poll_interval=2, timeout=180):
    """
    Block until avatar/profile appears or timeout.
    """
    start = time.time()
    # print("Please log in manually in the opened Chrome window...")
    while True:
        status = check_login_status(driver, timeout=4)
        if status == "logged_in":
            print("Detected logged-in state.")
            return True
        if time.time() - start > timeout:
            print("Timeout waiting for manual login.")
            return False
        if status == "not_logged_in":
            print("Login button still visible. Please complete login.")
        else:
            print("Still waiting for login bar/status…")
        time.sleep(poll_interval)


def get_driver_with_profile():
    options = Options()
    user = getpass.getuser()
    # custom_user_data_dir = f"C:/Users/{user}/AppData/Local/Google/Chrome/Instagram_Bot" # Use a dedicated profile folder for instagram instead
    custom_user_data_dir = f"/Users/{user}/Library/Application Support/Google/Chrome/Instagram_Bot" # Use a dedicated profile folder for instagram instead
    options.add_argument(f"--user-data-dir={custom_user_data_dir}")
    options.add_experimental_option("detach", True)
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--log-level=3")
    options.add_argument("--disable-logging")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    return driver


def find_and_like_comments(driver, link, max_scrolls=MAX_SCROLLS):
    """
    Finds the comments section on an Instagram post and likes comments.
    No need to click comment button - comments are already visible.
    """
    try:
        print(f"\n{'='*60}")
        print(f"Processing: {link}")
        print(f"{'='*60}")
        
        # Navigate to the post
        driver.get(link)
        human_sleep(2.0, 3.5)

        # Wait for page to be fully loaded
        try:
            WebDriverWait(driver, 15).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            print("✓ Page loaded")
        except Exception as e:
            print(f"Page load timeout: {e}")

        # Additional wait for dynamic content
        human_sleep(1.5, 2.5)

        # Check if comments section exists and is visible
        comments_container = None
        
        print("\nSearching for comments container...")

        # The Click logic
        try:
            # Find the clickable button directly by the SVG
            comment_button = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((
            By.CSS_SELECTOR,
            "div[role='button'] svg[aria-label='Comment']"
        ))
    )
            
            # Get the button (parent)
            button = comment_button.find_element(By.XPATH, "./ancestor::div[@role='button']")
            
            human_sleep(0.3, 0.6)
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
            human_sleep(0.3, 0.6)
            
            driver.execute_script("arguments[0].click();", button)
            print("Clicked comment button")
            print("comment button found")
            human_sleep(0.3, 0.6)
            
                
        except Exception as e:
            print(f"comment button not found {e}")


        # This is the div that holds all individual comment blocks
        try:
            comments_container = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((
                    By.XPATH,
                    # "//div[contains(@class,'x78zum5') and contains(@class,'xdt5ytf') and contains(@class,'x1iyjqo2')]"
                    "//div[@class='x78zum5 xdt5ytf x1iyjqo2 xh8yej3']"
                    
                ))
            )
            
            # Verify it's actually visible
            if not comments_container.is_displayed():
                print("✗ Comments container exists but is not visible")
            else: 
                print("✓ Comments container found!")
                # return 0
                
        except Exception:
            print("✗ Comments container not found on page")
            return 0
        except Exception as e:
            print(f"✗ Error finding comments container: {e}")
            return 0

        # Verify there are actual comments inside
        try:
            # Check for at least one comment block "Individual comment paths"

            test_comments = comments_container.find_elements(
                By.XPATH,
                ".//div[@class='html-div xdj266r x14z9mp xat24cr x1lziwak xyri2b x1c1uobl x9f619 xjbqb8w x78zum5 x15mokao x1ga7v0g" \
                " x16uus16 xbiv7yw xsag5q8 xz9dl7a x1uhb9sk x1plvlek xryxfnj x1c4vz4f x2lah0s x1q0g3np xqjyukv x1qjc9v5 x1oa3qoh x1nhvcw1']"
            )
            
            if len(test_comments) == 0:
                print("✗ No comments found in container")
                return 0
            
            print(f"✓ Found {len(test_comments)} initial comment blocks")
            
        except Exception as e:
            print(f"Error checking for comments: {e}")
            return 0

        # Start scrolling and liking
        print("\n" + "="*60)
        print("Starting to scroll and like comments...")
        print("="*60 + "\n")
        # send the comment container to the function
        likes_count = scroll_and_like_comments(driver, comments_container, test_comments, max_scrolls)
        
        return likes_count

    except Exception as e:
        print(f"\n✗ Error processing post: {e}")
        print(traceback.format_exc())
        return 0


def scroll_and_like_comments(driver, comments_container, test_comments, max_scrolls=MAX_SCROLLS):
    """
    Scroll the comments section and like comments as they come into view.
    """
    print("\n=== Starting comment liking process ===")
    seen_comments = set()
    likes_count = 0
    stagnant_loops = 0
    MAX_STAGNANT_LOOPS = 5

    for i in range(max_scrolls):
        print(f"\n--- Scroll iteration {i+1}/{max_scrolls} ---")

        # Occasional longer pause
        if random.random() < LONG_PAUSE_PROB:
            pause = random.uniform(LONG_PAUSE_MIN, LONG_PAUSE_MAX)
            print(f"Taking a longer pause for {pause:.1f}s")
            time.sleep(pause)

        # Scroll the comments container
        if i > 0:  # Don't scroll on first iteration
            scrolled = False
            for s_try in range(SCROLL_RETRY_ATTEMPTS):
                try:
                    # Scroll within the comments container
                    driver.execute_script(
                        "arguments[0].scrollTop += arguments[1];",
                        comments_container,
                        random.randint(400, 800)
                    )
                    scrolled = True
                    print("✓ Scrolled successfully")
                    break
                except Exception as e:
                    print(f"Scroll attempt {s_try+1}/{SCROLL_RETRY_ATTEMPTS} failed: {e}")
                    human_sleep(0.3, 0.8)
            
            if not scrolled:
                print("Unable to scroll; breaking out")
                break

            human_sleep(0.8, 1.5)

        print(f"Found {len(test_comments)} comment blocks in view")
        

        # Find all comment blocks in the current view
        try:
            # Finds each individual comment and like section, which will be used to with individual comments and likes
            comment_and_like_blocks = comments_container.find_elements(
                By.XPATH,
                ".//div[@class='html-div xdj266r x14z9mp xat24cr x1lziwak xexx8yu xyri2b x18d9i69 x1c1uobl x9f619 xjbqb8w x78zum5" \
                " x15mokao x1ga7v0g x16uus16 xbiv7yw x1uhb9sk x1plvlek xryxfnj x1iyjqo2 x2lwn1j xeuugli x1q0g3np xqjyukv x1qjc9v5 x1oa3qoh x1nhvcw1']"
            )

            # comment_blocks = comment_and_like_blocks.find_elements(
            #     By.XPATH,
            #     ".//div[@class='html-div xdj266r x14z9mp xat24cr x1lziwak xexx8yu xyri2b x18d9i69 x1c1uobl x9f619 xjbqb8w x78zum5 x15mokao " \
            #     "x1ga7v0g x16uus16 xbiv7yw x1uhb9sk x1plvlek xryxfnj x1iyjqo2 x2lwn1j xeuugli xdt5ytf xqjyukv x1qjc9v5 x1oa3qoh x1nhvcw1']"
            # )

            # like_blocks = comment_and_like_blocks.find_element(
            #             By.XPATH,
            #             ".//span[@class='xjkvuk6']"
            #         )
            
            
        except Exception as e:
            print(f"Error finding comments: {e}")
            continue

        if not comment_and_like_blocks:
            print("No comment blocks found")
            if i > 5:
                break
            continue

        before_count = len(seen_comments)
  
        for idx, comment_block in enumerate(comment_and_like_blocks):
            try:
                # Extract username
                username = ""
                try:
                    username_elem = comment_block.find_element(
                        By.XPATH,
                        ".//span[@class='_ap3a _aaco _aacw _aacx _aad7 _aade']"
                    )
                    username = username_elem.text.strip()
                except:
                    pass

                # Extract comment text
                comment_text = ""
                try:
                    # Find the span that contains the actual comment text (not username, not time)
                    text_spans = comment_block.find_elements(
                        By.XPATH,
                        ".//span[contains(@class,'x193iq5w') and contains(@class,'xeuugli') and contains(@class,'x1fj9vlw')]"
                    )
                    # Get the span with actual content
                    for span in text_spans:
                        text = span.text.strip()
                        if text and text != username and not text.endswith('w') and not text.endswith('d') and not text.endswith('h'):
                            comment_text = text
                            break
                except:
                    pass

                # Create unique identifier
                unique_key = f"{username}:{comment_text[:100]}" if username else comment_text[:100]
                
                if not unique_key or unique_key in seen_comments:
                    continue
                
                # Display info about current comment
                display_text = comment_text[:50] + "..." if len(comment_text) > 50 else comment_text
                print(f"\n[{len(seen_comments)}] @{username}: {display_text}")

                # Random skip for human-like behavior
                # if random.random() < SKIP_PROB:
                #     print(" → Skipping (random)")
                #     human_sleep(0.2, 0.6)
                    # continue


                 # Scroll comment into view
                try:
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center', behavior: 'smooth'});",
                        comment_block
                    )
                    human_sleep(0.8, 1.5)
                except:
                    pass

                like_button = None
                already_liked = False

                try:
                    # First, find the container that has the like button
                    human_sleep(0.3, 0.8)
                    like_block = comment_block.find_element(
                        By.XPATH,
                        ".//span[@class='xjkvuk6']"
                    )

                    button= like_block.find_element(By.XPATH, ".//div[@role='button']")
    
                    if button.is_displayed():
                        try:
                            # Find SVG using tag name (more reliable)
                            svgs = button.find_elements(By.TAG_NAME, "svg")
                            
                            if svgs:
                                svg = svgs[0]
                                aria_label = svg.get_attribute("aria-label")
                                
                                print(f"  ℹ Button found with SVG aria-label: '{aria_label}'")
                                
                                if aria_label == "Like":
                                    print(f"  ✓ Clicking 'Like' button...")
                                    driver.execute_script("arguments[0].scrollIntoView(true);", button)
                                    human_sleep(0.6, 0.8)
                                    
                                    try:
                                        button.click()

                                    except:
                                        driver.execute_script("arguments[0].click();", button)
                                    
                                    svgs = button.find_elements(By.TAG_NAME, "svg")

                                    if not svgs:
                                        continue

                                    svg = svgs[0]
                                    aria_label = svg.get_attribute("aria-label")

                                    if aria_label == "Like":
                                        print(f"  ✓ Clicking 'Like' button...")
                                        driver.execute_script("arguments[0].scrollIntoView(true);", button)
                                        human_sleep(0.6, 0.8)
                                        
                                        try:
                                            button.click()

                                        except:
                                            driver.execute_script("arguments[0].click();", button)
                                        else: 
                                            pass

                                    try:
                                        if aria_label == "Unlike":
                                            already_liked = True
                                    except:
                                        while aria_label != "Like":
                                            human_sleep(0.3, 0.6)
                                            try:
                                                button.click()

                                            except:
                                                driver.execute_script("arguments[0].click();", button)
                                            
                                    print(f"  ✓ Liked comment")
                                    likes_count += 1
                                    seen_comments.add(unique_key)
                                    human_sleep(0.5, 1)
                                    
                                elif aria_label == "Unlike":
                                    print(f"  ⊘ Already liked - skipping")
                                    seen_comments.add(unique_key)
                                    # break
                                else:
                                    print(f"  ? Unknown aria-label '{aria_label}' - skipping")
                                    # break
                                    
                        except Exception as svg_error:
                            continue  
                    
                except Exception as e:
                    print(f"  ✗ Error clicking like: {e}")
                    human_sleep(0.3, 0.8)

            except Exception as e:
                print(f"Error processing comment: {e}")
                continue

        # Check for stagnation (no new comments)
        if len(seen_comments) == before_count:
            stagnant_loops += 1
            print(f"\n No new comments loaded. Stagnant: {stagnant_loops}/{MAX_STAGNANT_LOOPS}")

            if stagnant_loops == 2:
                print(" Task completed - no new comments found after two (2) scrolls.")
                break
            
            if stagnant_loops >= MAX_STAGNANT_LOOPS:
                print(f"Reached end of comments {MAX_STAGNANT_LOOPS} times. Stopping.")
                break
        else:
            stagnant_loops = 0

        # Occasional scroll up (human behavior)
        if random.random() < 0.08:
            try:
                driver.execute_script(
                    "arguments[0].scrollTop -= arguments[1];",
                    comments_container,
                    random.randint(80, 250)
                )
                human_sleep(0.4, 1.0)
            except:
                pass

        # Progress update every 10 scrolls
        if i % 10 == 0 and i > 0:
            print(f"\n📊 Progress: {likes_count} likes | {len(seen_comments)} comments seen")

    print(f"\n{'='*60}")
    print(f"✓ Finished: {likes_count} likes | {len(seen_comments)} comments processed")
    print(f"{'='*60}\n")
    
    return likes_count


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
                print("Bypassing login.")
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
                # print(f"Processing link: {link}")
                
                # Find the comment container and like comments
                comments_section = find_and_like_comments(driver, link, max_scrolls=MAX_SCROLLS)
                if not comments_section:
                    print(f"Skipping {link}: couldn't open comments after retries.")
                    continue

                print("Comments panel opened. Starting scroll-and-like routine...")
                # liked = scroll_and_like_comments(driver, comments_section, max_scrolls=MAX_SCROLLS)
                # print(f"Done with this post: liked {liked} comments on {link}")
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
        print("No valid links provided. Please add links to video_links.txt or check your internet connection.")
