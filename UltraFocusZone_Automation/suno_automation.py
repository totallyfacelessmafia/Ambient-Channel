import os
import time
import json
import re
import requests
from pathlib import Path
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import undetected_chromedriver as uc
import sys

"""
Suno AI Automation - Automatic Music Generation
Generates music tracks automatically using Suno.ai
"""



class SunoAutomation:
    """Automates Suno.ai music generation and download"""

    def __init__(self, email=None, password=None, headless=False, download_dir=None, timeout=300, profile_dir=None, create_url=None):
        self.email = email
        self.password = password
        self.headless = headless
        self.driver = None
        self.wait = None
        self.timeout = timeout
        self.download_dir = Path(download_dir or Path.cwd() / "suno_outputs")
        # Optional Chrome user data directory (so you can sign in interactively once)
        self.profile_dir = Path(profile_dir) if profile_dir else None
        # Optional URL to open for creation (defaults to Suno create page)
        self.create_url = create_url
        self.download_dir.mkdir(parents=True, exist_ok=True)

    def setup_driver(self):
        """Setup Chrome driver with undetected-chromedriver and set download prefs"""
        try:
            print("🌐 Setting up Chrome browser...")
            options = uc.ChromeOptions()
            # If a profile dir is provided, use it so browser sessions can persist login state
            if self.profile_dir:
                self.profile_dir.mkdir(parents=True, exist_ok=True)
                options.add_argument(f"--user-data-dir={str(self.profile_dir.resolve())}")
            if self.headless:
                options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--disable-blink-features=AutomationControlled")
            prefs = {
                "download.default_directory": str(self.download_dir.resolve()),
                "download.prompt_for_download": False,
                "profile.default_content_setting_values.automatic_downloads": 1,
                "safebrowsing.enabled": True,
            }
            options.add_experimental_option("prefs", prefs)
            self.driver = uc.Chrome(options=options)
            self.wait = WebDriverWait(self.driver, 30)
            print("✅ Browser ready")
            return True
        except Exception as e:
            print(f"❌ Browser setup failed: {e}")
            return False

    def login(self):
        """Attempt to log in. If login elements not found, skip (Suno may allow guest usage)."""
        if not (self.email and self.password):
            print("ℹ️ No credentials provided, skipping login.")
            return True

        try:
            print("🔐 Logging into Suno.ai...")
            target_url = self.create_url or "https://suno.com/create"
            self.driver.get(target_url)
            # Wait a bit for page to load
            time.sleep(3)

            # Try common "Log in" / "Sign in" flows
            try:
                btn = self.wait.until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//button[contains(., 'Log in') or contains(., 'Sign in')]")
                    )
                )
                btn.click()
                time.sleep(1)
            except TimeoutException:
                pass

            # Find email and password inputs
            try:
                email_el = self.wait.until(
                    EC.presence_of_element_located(
                        (By.XPATH, "//input[@type='email' or contains(@placeholder,'Email')]")
                    )
                )
                email_el.clear()
                email_el.send_keys(self.email)
            except TimeoutException:
                print("⚠️ Email input not found; login may be handled externally.")
                return True

            try:
                pwd_el = self.driver.find_element(By.XPATH, "//input[@type='password' or contains(@placeholder,'Password')]")
                pwd_el.clear()
                pwd_el.send_keys(self.password)
            except NoSuchElementException:
                # Some sites have a next button before password
                try:
                    email_el.send_keys(Keys.ENTER)
                    time.sleep(1)
                    pwd_el = self.wait.until(
                        EC.presence_of_element_located((By.XPATH, "//input[@type='password']"))
                    )
                    pwd_el.clear()
                    pwd_el.send_keys(self.password)
                except Exception:
                    print("⚠️ Password input not found; aborting login attempt.")
                    return False

            # Submit form
            try:
                pwd_el.send_keys(Keys.ENTER)
            except Exception:
                try:
                    submit = self.driver.find_element(By.XPATH, "//button[contains(., 'Log in') or contains(., 'Sign in') or @type='submit']")
                    submit.click()
                except Exception:
                    pass

            # Wait for some indication of logged-in state or homepage change
            time.sleep(3)
            print("✅ Login attempted")
            return True
        except Exception as e:
            print(f"❌ Login failed: {e}")
            return False

    def _set_session_cookies(self, session):
        """Copy browser cookies to requests session for authenticated downloads"""
        for c in self.driver.get_cookies():
            session.cookies.set(c["name"], c["value"], domain=c.get("domain"))

    def generate(self, prompt, style=None, length_seconds=None, repeats=1, per_click=2, per_asset_timeout=60):
        """
        Create a generation on Suno.ai using the given prompt.
        Selectors are best-effort; site changes may require updates.
        Returns path to downloaded file or None.
        """
        print(f"🎛️ Generating prompt: {prompt!r}")
        outputs = []
        try:
            # Navigate to the create page where songs are generated
            self.driver.get(self.create_url or "https://suno.com/create")
            time.sleep(3)

            # Find a prompt input (textarea or contenteditable)
            try:
                prompt_el = self.wait.until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//textarea | //div[@contenteditable='true']")
                    )
                )

                try:
                    prompt_el.click()
                except Exception:
                    # Sometimes overlays intercept clicks (e.g. sign-in footer). Try JS fallback.
                    try:
                        self.driver.execute_script("arguments[0].scrollIntoView(true);", prompt_el)
                        self.driver.execute_script("arguments[0].click();", prompt_el)
                    except Exception:
                        pass

                # clear existing
                try:
                    prompt_el.clear()
                except Exception:
                    try:
                        # For contenteditable or stubborn fields, use JS to clear
                        self.driver.execute_script("if(arguments[0].isContentEditable){arguments[0].innerText='';}else{arguments[0].value='';}", prompt_el)
                    except Exception:
                        try:
                            prompt_el.send_keys(Keys.CONTROL + "a")
                            prompt_el.send_keys(Keys.BACKSPACE)
                        except Exception:
                            pass

                # Try to set prompt via JS if send_keys fails
                sent = False
                try:
                    prompt_el.send_keys(prompt)
                    sent = True
                except Exception:
                    try:
                        # handle textarea/input
                        self.driver.execute_script("if(arguments[0].isContentEditable){arguments[0].innerText=arguments[1];}else{arguments[0].value=arguments[1];}arguments[0].dispatchEvent(new Event('input'));", prompt_el, prompt)
                        sent = True
                    except Exception:
                        sent = False

                if not sent:
                    print("⚠️ Could not type prompt into Suno UI (will try alternate interaction).")
                    try:
                        pass
                    except Exception:
                        pass
            except TimeoutException:
                print("❌ Prompt input not found on page.")
                return None

            # Optionally choose style/length via UI - best-effort clicks
            if style:
                try:
                    style_btn = self.driver.find_element(By.XPATH, f"//button[contains(., '{style}')]")
                    style_btn.click()
                    time.sleep(0.5)
                except Exception:
                    pass

            if length_seconds:
                # Some UIs have length presets; best-effort
                try:
                    length_btn = self.driver.find_element(By.XPATH, f"//button[contains(., '{length_seconds}s') or contains(., '{length_seconds} sec')]")
                    length_btn.click()
                    time.sleep(0.5)
                except Exception:
                    pass

            # Perform repeated generate clicks if requested. Some Suno flows generate multiple
            # songs per click (e.g. 2). We'll click `repeats` times and attempt to download
            # `per_click` assets per click.
            for r in range(max(1, int(repeats))):
                # Find generate button each iteration
                try:
                    gen_btn = self.driver.find_element(By.XPATH, "//button[contains(., 'Generate') or contains(., 'Create') or contains(., 'Compose') or contains(., 'Create song')]")
                    try:
                        gen_btn.click()
                    except Exception:
                        try:
                            self.driver.execute_script("arguments[0].click();", gen_btn)
                        except Exception:
                            print("⚠️ Could not click Generate button.")
                except Exception:
                    # try form submit fallback
                    try:
                        prompt_el.send_keys(Keys.ENTER)
                    except Exception:
                        try:
                            self.driver.execute_script("arguments[0].dispatchEvent(new KeyboardEvent('keydown',{'key':'Enter'}));", prompt_el)
                        except Exception:
                            print("❌ Could not start generation (no Generate button).")
                            break

                print(f"⏳ Waiting for generation #{r+1} to complete...")
                # after clicking, try to download expected number of assets for this click
                for i in range(max(1, int(per_click))):
                    out = self._wait_for_and_download_asset(per_asset_timeout)
                    if out:
                        outputs.append(out)
                        print(f"✅ Downloaded: {out}")
                    else:
                        print("⚠️ No asset found for this generation iteration.")

            if outputs:
                return outputs
            else:
                print("❌ Generation finished but no downloadable assets were found.")
                return []
        except Exception as e:
            # Save debug HTML
            try:
                dbg_name = f"suno_error_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.html"
                dbg_path = self.download_dir / dbg_name
                with open(dbg_path, "w", encoding="utf-8") as f:
                    f.write(self.driver.page_source)
                print(f"ℹ️ Generation error — saved page HTML to: {dbg_path}")
            except Exception:
                pass
            print(f"❌ Generation failed: {e}")
            return []

    def _wait_for_and_download_asset(self, per_asset_timeout=None):
        """
        Poll page for audio asset links (mp3/wav/m4a). When found, download via requests
        using browser cookies. Returns path or None.
        """
        timeout_seconds = per_asset_timeout if per_asset_timeout is not None else self.timeout
        timeout = time.time() + timeout_seconds
        asset_url = None
        while time.time() < timeout:
            # look for anchors with audio files
            anchors = self.driver.find_elements(By.XPATH, "//a[contains(@href, '.mp3') or contains(@href, '.wav') or contains(@href, '.m4a')]")
            for a in anchors:
                href = a.get_attribute("href")
                if href and re.search(r"\.(mp3|wav|m4a)(\?|$)", href, re.I):
                    asset_url = href
                    break
            if asset_url:
                break
            # sometimes a download button exists
            try:
                dl_btn = self.driver.find_element(By.XPATH, "//button[contains(., 'Download') or contains(., 'download')]")
                # try to extract anchor inside button
                try:
                    a = dl_btn.find_element(By.XPATH, ".//a")
                    href = a.get_attribute("href")
                    if href and re.search(r"\.(mp3|wav|m4a)(\?|$)", href, re.I):
                        asset_url = href
                        break
                except Exception:
                    # click download button to trigger link rendering
                    try:
                        dl_btn.click()
                    except Exception:
                        pass
            except Exception:
                pass

            time.sleep(2)

        if not asset_url:
            # Try song-detail pages: collect links like /song/<id> and visit each to find audio
            try:
                song_links = []
                anchors = self.driver.find_elements(By.XPATH, "//a[starts-with(@href, '/song/')]")
                for a in anchors:
                    href = a.get_attribute("href") or a.get_attribute("href")
                    if href:
                        # normalize to path-only if absolute
                        m = re.search(r"(/song/[a-zA-Z0-9\-]+)", href)
                        if m:
                            path = m.group(1)
                            if path not in song_links:
                                song_links.append(path)

                for path in song_links:
                    full = f"https://suno.com{path}"
                    try:
                        # open song detail in a new tab
                        self.driver.execute_script("window.open('about:blank');")
                        handles = self.driver.window_handles
                        self.driver.switch_to.window(handles[-1])
                        self.driver.get(full)
                        time.sleep(2)

                        # Priority 1: <audio src=...> or <audio><source src=...>
                        try:
                            audio_els = self.driver.find_elements(By.TAG_NAME, 'audio')
                            for a_el in audio_els:
                                src = a_el.get_attribute('src')
                                if src and re.search(r"\.(mp3|wav|m4a)(\?|$)", src, re.I):
                                    asset_url = src
                                    break
                                # check source children
                                try:
                                    src_children = a_el.find_elements(By.TAG_NAME, 'source')
                                    for s in src_children:
                                        ssrc = s.get_attribute('src')
                                        if ssrc and re.search(r"\.(mp3|wav|m4a)(\?|$)", ssrc, re.I):
                                            asset_url = ssrc
                                            break
                                    if asset_url:
                                        break
                                except Exception:
                                    pass
                        except Exception:
                            pass

                        # Priority 2: meta og:audio
                        if not asset_url:
                            try:
                                meta = self.driver.find_elements(By.XPATH, "//meta[@property='og:audio' or @name='og:audio']")
                                for m in meta:
                                    content = m.get_attribute('content')
                                    if content and re.search(r"\.(mp3|wav|m4a)(\?|$)", content, re.I):
                                        asset_url = content
                                        break
                            except Exception:
                                pass

                        # Priority 3: look for CDN audio URLs inside page source JSON/scripts
                        if not asset_url:
                            try:
                                src_text = self.driver.page_source
                                m = re.search(r'https?://[^"\s>]+?\.(?:mp3|m4a|wav)(?:\?[^"\s>]*)?', src_text, re.I)
                                if m:
                                    asset_url = m.group(0)
                            except Exception:
                                pass

                        # If found, attempt download
                        if asset_url:
                            # sanitize filename
                            fname = Path(asset_url.split("?")[0]).name
                            out_path = self.download_dir / fname
                            session = requests.Session()
                            self._set_session_cookies(session)
                            try:
                                with session.get(asset_url, stream=True, timeout=60) as r:
                                    r.raise_for_status()
                                    with open(out_path, "wb") as f:
                                        for chunk in r.iter_content(chunk_size=8192):
                                            if chunk:
                                                f.write(chunk)
                                # close the tab and switch back
                                self.driver.close()
                                self.driver.switch_to.window(handles[0])
                                return str(out_path.resolve())
                            except Exception as e:
                                print(f"⚠️ Failed to download from song page {full}: {e}")

                        # close the tab and go back
                        try:
                            self.driver.close()
                        except Exception:
                            pass
                        self.driver.switch_to.window(handles[0])
                    except Exception:
                        # ensure we return to main window on failure
                        try:
                            self.driver.switch_to.window(self.driver.window_handles[0])
                        except Exception:
                            pass

            except Exception as e:
                print(f"⚠️ Song-detail fallback failed: {e}")

            # Save page HTML for debugging so we can inspect why no asset link appeared
            try:
                dbg_name = f"suno_page_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.html"
                dbg_path = self.download_dir / dbg_name
                with open(dbg_path, "w", encoding="utf-8") as f:
                    f.write(self.driver.page_source)
                print(f"ℹ️ No asset found — saved page HTML to: {dbg_path}")
            except Exception as e:
                print(f"⚠️ Could not save debug HTML: {e}")
            return None

        # sanitize filename
        fname = Path(asset_url.split("?")[0]).name
        if not fname:
            fname = f"suno_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.mp3"
        out_path = self.download_dir / fname

        # download using requests with cookies from browser for auth
        session = requests.Session()
        self._set_session_cookies(session)
        try:
            with session.get(asset_url, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(out_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
            return str(out_path.resolve())
        except Exception as e:
            print(f"⚠️ Direct download failed ({e}), attempting browser click fallback...")
            # fallback: try clicking the anchor to let browser download to preset folder
            try:
                # find corresponding anchor and click
                elems = self.driver.find_elements(By.XPATH, f"//a[contains(@href, '{fname}')]")
                if elems:
                    elems[0].click()
                    # wait for file to appear in download_dir
                    dl_timeout = time.time() + 60
                    while time.time() < dl_timeout:
                        files = list(self.download_dir.glob("*"))
                        for f in files:
                            if f.name == fname:
                                return str(f.resolve())
                        time.sleep(1)
            except Exception:
                pass
            return None

    def close(self):
        try:
            if self.driver:
                self.driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    # Minimal CLI usage: set SUNO_EMAIL and SUNO_PASSWORD env vars or leave blank

    email = os.getenv("SUNO_EMAIL")
    password = os.getenv("SUNO_PASSWORD")
    prompt = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Calm lo-fi instrumental, 2 minutes"
    sa = SunoAutomation(email=email, password=password, headless=False)
    if not sa.setup_driver():
        raise SystemExit(1)
    try:
        if not sa.login():
            print("Login reported failure; continuing anyway.")
        out = sa.generate(prompt)
        if out:
            print(out)
        else:
            print("No output produced.")
    finally:
        sa.close()