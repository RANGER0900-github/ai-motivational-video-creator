# Instagram 9:16 Upload Guide

## Goal

Upload a generated video from `outputs/` to Instagram as a proper vertical reel, keeping the full `9:16` framing instead of letting Instagram shrink or crop it incorrectly.

This document records the exact workflow that worked.

## Working setup

### Important files

- Uploader script: [scripts/ig_upload_playwright.py](/home/meet/projects/ai-video-gen/scripts/ig_upload_playwright.py)
- Cookie file used for the logged-in session: `/home/meet/Downloads/cookies.txt`
- Saved Playwright storage file: [state/ig_storage.json](/home/meet/projects/ai-video-gen/state/ig_storage.json)
- Output videos folder: [outputs](/home/meet/projects/ai-video-gen/outputs)
- Debug artifacts folder: [state](/home/meet/projects/ai-video-gen/state)

### Account used during testing

- Instagram account: `_thecoco_club`

### Confirmed successful reel URLs

- Earlier success: `https://www.instagram.com/_thecoco_club/reel/DWweIRfid1A/`
- Final `9:16` success: `https://www.instagram.com/_thecoco_club/reel/DWwieRXmB0f/`
- Final `9:16 + caption` success: `https://www.instagram.com/_thecoco_club/reel/DWwj_DKjMWo/`

## Exact steps that worked

### 1. Do not log in by username/password in automation

That path was unreliable because Instagram triggered:

- invalid login responses
- recaptcha/challenge screens
- automation blocking

The reliable approach was:

1. Log into Instagram manually in Chrome.
2. Export cookies.
3. Save them as:
   `/home/meet/Downloads/cookies.txt`

That cookie file was enough to reuse the session in Playwright.

## 2. Use the Playwright uploader script

The working script is:

- [scripts/ig_upload_playwright.py](/home/meet/projects/ai-video-gen/scripts/ig_upload_playwright.py)

It uses:

- the newest `.mp4` from [outputs](/home/meet/projects/ai-video-gen/outputs)
- the cookie file at `/home/meet/Downloads/cookies.txt`
- headless Chromium by default

Run it with:

```bash
source .venv/bin/activate
python3 scripts/ig_upload_playwright.py
```

## 3. Exact Instagram upload flow

This is the actual sequence that worked:

1. Open Instagram home.
2. Click `Create`.
3. Click the second `Post` option in the create menu.
4. Set the hidden `input[type="file"]` directly with the MP4 file path.
5. Wait for the crop composer to load.
6. If the reels info modal appears, click `OK`.
7. Open the crop menu from the bottom-left crop icon.
8. Select `9:16`.
9. Close the crop menu again.
10. Click top-right `Next`.
11. Click top-right `Next` again on the edit screen.
12. On the `New reel` screen, fill the caption field.
13. Click top-right `Share`.
14. Wait until the UI shows `Sharing`.

## 3A. Exact caption flow that worked

The uploader now adds a real Instagram caption before publishing.

Source of caption data:

- quote: read from `state/app.db`
- author: read from `state/app.db`
- selected video: matched from the chosen file in `outputs/`

The caption format that worked was:

1. Quote text
2. Author line
3. Blank line
4. Short CTA line
5. Blank line
6. Hashtag block

Exact example from the successful reel:

```text
The successful warrior is the average man, with laser-like focus.
— Bruce Lee

Save this reel and come back when you need the reminder.

#motivation #mindset #discipline #selfimprovement #success #focus #reels #explorepage #viralreels #motivationdaily
```

The uploader fills this into the `Write a caption...` field on the `New reel` screen before clicking `Share`.

## 4. The crucial 9:16 fix

This was the main issue.

Earlier uploads published successfully, but the reel looked wrong because the script never controlled the crop selector.

The crop UI exposes these options:

- `Original`
- `1:1`
- `9:16`
- `16:9`

The correct fix was:

1. Open the crop selector.
2. Explicitly choose `9:16`.
3. Close the crop selector.
4. Only then continue to `Next`.

Without this, Instagram was keeping the wrong crop state and the reel looked shrunk.

## 5. Exact crop controls discovered by scraping

These were the important controls found in the crop dialog:

- top-right action: `Next`
- bottom-left icon: `Select crop`
- crop options inside the dialog:
  - `Original`
  - `1:1`
  - `9:16`
  - `16:9`

The crop toggle is not a simple labeled button. The stable way was to target:

- `svg[aria-label="Select crop"]`
- then click its nearest ancestor button/role-button

The crop options themselves were found as:

- dialog-scoped `[role="button"]` entries

## 6. Why the earlier version failed

The earlier uploader had several false positives.

### Mistake 1: “Feed returned” was treated as success

That was wrong.

Returning to the feed did not prove a reel was published.

### Mistake 2: Generic text clicking

Using broad selectors like:

- `text=Next`
- `text=Share`
- generic “visible text click”

caused the script to hit the wrong element in the composer.

That caused:

- staying on crop
- opening `Discard post?`
- thinking the post was done when it was not

### Mistake 3: No explicit crop handling

This is what caused the “shrunk reel” result.

The script published the video, but it never selected `9:16`.

## 7. What the script must always do now

If this uploader is reused, the non-negotiable sequence is:

1. cookie-based auth
2. upload file directly
3. handle reels modal
4. open crop menu
5. select `9:16`
6. close crop menu
7. `Next`
8. `Next`
9. fill caption from quote + author + CTA + hashtags
10. `Share`
11. verify the new reel URL

## 8. How to verify the upload really worked

Do not trust just one signal.

Use all of these:

### Verification 1: Composer state

Confirm the composer reaches:

- `New reel`
- then `Sharing`

### Verification 2: Reels tab

Open:

- `https://www.instagram.com/_thecoco_club/reels/`

Confirm there is a new top reel tile.

### Verification 3: Direct reel URL

Open the newest reel URL directly and confirm:

- it loads
- it says something like `1 minute ago`
- the page contains a real `<video>`
- the caption text is present
- the hashtags are present

### Verification 4: Video dimensions

From the successful final run, Playwright reported:

- width: `720`
- height: `1280`
- duration: `17`

That confirms the uploaded reel itself is proper `9:16`.

## Final successful verification

The final correct `9:16` upload produced:

- reel URL: `https://www.instagram.com/_thecoco_club/reel/DWwieRXmB0f/`
- reel timestamp: `1 minute ago`
- video dimensions: `720x1280`
- duration: `17`

The final correct `9:16 + caption` upload produced:

- reel URL: `https://www.instagram.com/_thecoco_club/reel/DWwj_DKjMWo/`
- quote shown in caption: `The successful warrior is the average man, with laser-like focus.`
- author shown in caption: `Bruce Lee`
- hashtags shown in caption:
  `#motivation #mindset #discipline #selfimprovement #success #focus #reels #explorepage #viralreels #motivationdaily`
- video dimensions: `720x1280`

## Important debug artifacts

These files are useful if the flow breaks again:

- crop composer after selecting `9:16`:
  [after_crop_9_16.png](/home/meet/projects/ai-video-gen/state/after_crop_9_16.png)
- final reels tab:
  [verify_reels.png](/home/meet/projects/ai-video-gen/state/verify_reels.png)
- newest reel detail page:
  [verify_reel_detail_latest.png](/home/meet/projects/ai-video-gen/state/verify_reel_detail_latest.png)
- newest reel detail page with caption verification:
  [verify_reel_caption_latest.png](/home/meet/projects/ai-video-gen/state/verify_reel_caption_latest.png)
- crop menu probe:
  [crop_menu_probe.png](/home/meet/projects/ai-video-gen/state/crop_menu_probe.png)

## If Instagram changes the UI again

Inspect these first:

1. active dialog count
2. whether the reels modal appears immediately after upload or after `Next`
3. the top-right `Next` / `Share` controls inside the main dialog
4. the bottom-left crop control
5. the crop option labels inside the crop menu

Do not switch back to broad page-wide text clicking unless there is no alternative.

## Minimal operator checklist

If you want to upload a new video to Instagram in proper `9:16`, follow this:

1. Make sure Chrome is already logged into the target Instagram account.
2. Export cookies to:
   `/home/meet/Downloads/cookies.txt`
3. Make sure the target `.mp4` is inside [outputs](/home/meet/projects/ai-video-gen/outputs).
4. Run:

```bash
source .venv/bin/activate
python3 scripts/ig_upload_playwright.py
```

5. After it finishes, verify:
   - newest reel appears on `/_thecoco_club/reels/`
   - newest reel URL opens
   - video reports `720x1280`
   - caption contains the quote, author, and hashtag block

That is the working end-to-end method.
