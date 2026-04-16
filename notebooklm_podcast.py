"""
NotebookLM podcast generator for GitHub Actions.
Loads Google auth state, creates notebook, generates Audio Overview, downloads MP3.
"""
import time, json, sys, os, base64, urllib.request

# Load auth state from environment variable
auth_b64 = os.environ.get("GOOGLE_AUTH_STATE", "")
if not auth_b64:
    print("ERROR: GOOGLE_AUTH_STATE not set")
    sys.exit(1)

auth_state = json.loads(base64.b64decode(auth_b64))
with open("/tmp/auth_state.json", "w") as f:
    json.dump(auth_state, f)
print(f"Loaded {len(auth_state.get('cookies', []))} cookies")

# Load briefing data
data_file = sys.argv[1] if len(sys.argv) > 1 else "/tmp/data.json"
with open(data_file) as f:
    d = json.load(f)

# Format briefing as readable text
TEXT = f"""MARKET BRIEFING — {d['date']}
Sentiment: {d['sentiment']}

{d.get('pulse','').replace('<strong>','').replace('</strong>','')}

INDICES:
""" + "\n".join(f"{r[0]:10s} {r[1]:>10s} {r[2]:>8s} {r[3]:>8s}" for r in d.get('indices',[])) + """

CURRENCIES:
""" + "\n".join(f"{r[0]:10s} {r[1]:>10s} {r[2]:>8s} {r[3]:>8s}" for r in d.get('currencies',[])) + """

COMMODITIES:
""" + "\n".join(f"{r[0]:10s} {r[1]:>10s} {r[2]:>8s} {r[3]:>8s}" for r in d.get('commodities',[])) + """

STOCKS:
""" + "\n".join(f"{r[0]:10s} {r[1]:>10s} {r[2]:>8s} {r[3]:>8s}" for r in d.get('stocks',[])) + f"""

PORTFOLIO ANALYSIS:
{d.get('portfolio','').replace('<strong>','').replace('</strong>','')}

KEY NEWS:
""" + "\n".join(f"{i+1}. {n['title']} — {n['text']}" for i, n in enumerate(d.get('news',[]))) + f"""

DAY AHEAD:
{d.get('day_ahead','').replace('<strong>','').replace('</strong>','')}
"""

print(f"Briefing: {len(TEXT)} chars")

from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(storage_state="/tmp/auth_state.json")
    page = context.new_page()

    # Step 1: Go to NotebookLM
    print("Navigating to NotebookLM...")
    page.goto("https://notebooklm.google.com", timeout=30000)
    time.sleep(5)
    print(f"URL: {page.url}")
    print(f"Title: {page.title()}")

    # Check if we're logged in
    if "accounts.google" in page.url:
        print("ERROR: Not authenticated — cookies may have expired")
        page.screenshot(path="/tmp/notebooklm_auth_fail.png")
        sys.exit(1)

    print("Authenticated!")

    # Step 2: Create new notebook
    print("\nCreating notebook...")
    crea_btn = page.locator('button:has-text("Crea"), button:has-text("Create")')
    crea_btn.first.click()
    time.sleep(4)

    # Step 3: Click "Text copiat" / "Copied text"
    print("Selecting text source...")
    text_btn = page.locator('button:has-text("Text copiat"), button:has-text("Copied text"), button:has-text("Paste text")')
    text_btn.first.click()
    time.sleep(2)

    # Step 4: Fill the dialog textarea
    print("Pasting briefing text...")
    dialog_ta = page.locator('textarea[placeholder*="text"], textarea[placeholder*="Enganxa"], textarea[placeholder*="Paste"]')
    if dialog_ta.count() == 0:
        # Fallback: last textarea on page
        all_ta = page.locator('textarea')
        dialog_ta = all_ta.nth(all_ta.count() - 1)
    dialog_ta.first.click()
    time.sleep(0.5)
    dialog_ta.first.fill(TEXT)
    time.sleep(2)

    # Step 5: Click Insert
    print("Inserting source...")
    insert_btn = page.locator('button:has-text("Insereix"), button:has-text("Insert")')
    insert_btn.first.click(force=True)
    time.sleep(8)
    print(f"Notebook URL: {page.url}")

    # Navigate to clean notebook URL (remove ?addSource=true)
    notebook_url = page.url.split("?")[0]
    page.goto(notebook_url)
    time.sleep(5)
    print(f"Clean URL: {page.url}")

    # Step 6: Trigger Audio Overview (click ONCE, then wait)
    print("\nTriggering Audio Overview...")
    # Try multiple label variants (Catalan, English, partial match)
    audio_found = False
    for label in ["Audio overview", "Audio Overview", "Audio summary",
                   "Resum d'àudio", "Resum d\u2019àudio", "Audio"]:
        try:
            el = page.get_by_text(label)
            if el.count() > 0:
                el.first.click()
                print(f"Clicked '{label}'")
                audio_found = True
                break
        except:
            pass

    if not audio_found:
        # Fallback: click by aria-label or class
        print("Trying aria-label fallback...")
        fallback = page.locator('[aria-label*="audio" i], [aria-label*="Audio"], [data-type*="audio"]')
        if fallback.count() > 0:
            fallback.first.click()
            print(f"Clicked fallback audio button")
            audio_found = True
        else:
            # Last resort: take screenshot and dump page text
            page.screenshot(path="/tmp/notebooklm_no_audio_btn.png")
            print("Page text (Studio panel area):")
            print(page.inner_text("body")[:2000])
            sys.exit(1)

    time.sleep(3)

    # Click Generate button if present (ONCE only)
    gen_btn = page.locator('button:has-text("Generate"), button:has-text("Genera")')
    if gen_btn.count() > 0:
        gen_btn.first.click()
        print("Clicked Generate")
    time.sleep(5)

    # Step 7: Wait for audio generation (up to 12 minutes)
    print("Waiting for audio generation (up to 12 min)...")
    audio_ready = False
    for i in range(72):  # 72 x 10s = 12 minutes
        time.sleep(10)
        content = page.content()

        # Check for play button appearing (completed audio)
        play_btns = page.locator('[aria-label*="play"], [aria-label*="Play"], [aria-label*="Reprodueix"]')
        if play_btns.count() > 0:
            print(f"  [{i*10}s] Audio ready!")
            audio_ready = True
            break

        # Check if generating text is gone but audio element exists
        if "generant" not in content.lower() and "generating" not in content.lower():
            # Might be done, check for download option
            three_dot = page.locator('[aria-label*="more"], [aria-label*="opcions"], [aria-label*="menu"]')
            if three_dot.count() > 0:
                print(f"  [{i*10}s] Generation may be complete")
                audio_ready = True
                break

        if i % 6 == 0:
            print(f"  [{i*10}s] Still generating...")

    if not audio_ready:
        print("Audio generation timed out")
        page.screenshot(path="/tmp/notebooklm_timeout.png")
        sys.exit(1)

    # Step 8: Download the MP3 via network interception
    print("\nDownloading audio...")
    time.sleep(3)

    # Method: intercept network requests to find the audio URL
    audio_urls = []
    def handle_response(response):
        url = response.url
        ct = response.headers.get("content-type", "")
        if "audio" in ct or url.endswith(".mp3") or "audio" in url:
            audio_urls.append(url)
            print(f"  Found audio URL: {url[:100]}")
    page.on("response", handle_response)

    # Click the play button to trigger audio loading
    print("Clicking play to trigger audio load...")
    play_btn = page.locator('[aria-label*="play" i], [aria-label*="Play"], [aria-label*="Reprodueix"]')
    if play_btn.count() > 0:
        play_btn.first.click()
        time.sleep(5)
    else:
        # Try clicking the audio item itself
        audio_items = page.locator('[class*="audio"], [class*="overview-card"]')
        if audio_items.count() > 0:
            audio_items.first.click()
            time.sleep(5)

    # Also try to find audio src directly from the DOM
    audio_src = page.evaluate("""() => {
        // Check for audio elements
        const audios = document.querySelectorAll('audio');
        for (const a of audios) {
            if (a.src) return a.src;
            const source = a.querySelector('source');
            if (source && source.src) return source.src;
        }
        // Check for any elements with audio-related data attributes
        const els = document.querySelectorAll('[data-audio-url], [data-src*="audio"]');
        for (const el of els) {
            return el.dataset.audioUrl || el.dataset.src || '';
        }
        return '';
    }""")

    if audio_src:
        audio_urls.append(audio_src)
        print(f"  Found audio src from DOM: {audio_src[:100]}")

    page.remove_listener("response", handle_response)

    if audio_urls:
        # Download the audio file
        print(f"Downloading from: {audio_urls[0][:100]}...")
        # Use the browser context's cookies to download
        response = page.evaluate("""async (url) => {
            const resp = await fetch(url);
            const blob = await resp.blob();
            const reader = new FileReader();
            return new Promise(resolve => {
                reader.onloadend = () => resolve(reader.result);
                reader.readAsDataURL(blob);
            });
        }""", audio_urls[0])
        # Save the base64 data
        if response and "base64," in response:
            b64_data = response.split("base64,")[1]
            import base64 as b64module
            audio_bytes = b64module.b64decode(b64_data)
            with open("/tmp/podcast.mp3", "wb") as f:
                f.write(audio_bytes)
            print(f"Saved podcast: {len(audio_bytes)} bytes")
        else:
            print("Failed to download audio via fetch")
    else:
        # Fallback: try the three-dot menu approach
        print("No audio URL found, trying menu download...")
        # Click all possible three-dot buttons
        dots = page.locator('button:has(span:has-text("more_vert")), [aria-label*="More" i], [aria-label*="opcions" i]')
        for i in range(dots.count()):
            try:
                dots.nth(i).click()
                time.sleep(1)
                # Try to click download in the menu
                with page.expect_download(timeout=10000) as dl_info:
                    dl_btn = page.get_by_text("Download").or_(page.get_by_text("Baixa"))
                    dl_btn.first.click()
                download = dl_info.value
                download.save_as("/tmp/podcast.mp3")
                print(f"Downloaded via menu: {os.path.getsize('/tmp/podcast.mp3')} bytes")
                break
            except:
                page.keyboard.press("Escape")
                time.sleep(0.5)
                continue
    print(f"Saved podcast to /tmp/podcast.mp3 ({os.path.getsize('/tmp/podcast.mp3')} bytes)")

    # Cleanup: delete the notebook to avoid clutter
    page.goto("https://notebooklm.google.com")
    time.sleep(3)

    browser.close()
    print("\nDone!")
