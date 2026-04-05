# Instagram 9:16 Upload Guide

## Goal

Upload a generated video from `outputs/` to Instagram as a real vertical reel for `void.to.victory`, keep the full `9:16` frame, include a caption, and verify that the reel is actually published.

## Current working setup

### Important files

- Uploader script: [scripts/ig_upload_playwright.py](/home/meet/projects/ai-video-gen/scripts/ig_upload_playwright.py)
- Working cookie file: `/home/meet/Downloads/cookies (2).txt`
- Refreshed Playwright storage: [state/ig_storage.json](/home/meet/projects/ai-video-gen/state/ig_storage.json)
- Output videos folder: [outputs](/home/meet/projects/ai-video-gen/outputs)
- Debug artifacts folder: [state](/home/meet/projects/ai-video-gen/state)

### Current target account

- Instagram account: `void.to.victory`

### Current verified success

- Working reel URL: `https://www.instagram.com/void.to.victory/reel/DWwuhSUDCeG/`

## What changed from the old `_thecoco_club` flow

The old guide was correct about the reel composer, but it assumed:

- the target account was `_thecoco_club`
- the session could be restored from `cookies.txt`

That is no longer true.

The current working path is:

- account target: `void.to.victory`
- cookie source: `/home/meet/Downloads/cookies (2).txt`
- Instagram may show a saved-profile picker first
- clicking `void.to.victory` can open a password modal
- the uploader must handle that before the create flow

## Exact working flow

### 1. Restore the saved `void.to.victory` session

1. Open Instagram.
2. If the saved-profile picker is shown, click `void.to.victory`.
3. If Instagram asks for a password, enter it.
4. Wait until the feed loads under the `void.to.victory` session.

This is now part of the automation path.

### 2. Open the reel composer

1. Open Instagram home.
2. Click `Create`.
3. Click `Post`.
4. Wait for the `Create new post` modal.

The important signal here is:

- `Create new post`
- `Drag photos and videos here`
- `Select from computer`

### 3. Upload the video correctly

The reliable order for this account is:

1. Use the real `Select from computer` chooser first.
2. If needed, fall back to direct `input[type="file"]` injection.

This mattered because the direct hidden-input path could trigger:

- `Something went wrong`
- `-1 files were not uploaded`

Chooser-first fixed that.

### 4. Reel composer steps

After the file is accepted:

1. If the reels info modal appears, click `OK`.
2. Open the crop selector.
3. Select `9:16`.
4. Close the crop selector.
5. Click `Next`.
6. On the edit screen, click `Next` again.
7. On `New reel`, fill the caption.
8. Click `Share`.
9. Wait until the UI shows `Sharing`.

## Working caption format

The uploader reads metadata from `state/app.db` and builds:

1. Quote
2. Author
3. Blank line
4. CTA
5. Blank line
6. Hashtag block

Example that was verified live:

```text
The successful warrior is the average man, with laser-like focus.
— Bruce Lee

Save this reel and come back when you need the reminder.

#motivation #mindset #discipline #selfimprovement #success #focus #reels #explorepage #viralreels #motivationdaily
```

## The crucial 9:16 fix

The crop selector must be controlled explicitly.

Available crop options:

- `Original`
- `1:1`
- `9:16`
- `16:9`

The correct flow is:

1. Open crop selector
2. Choose `9:16`
3. Close crop selector
4. Only then continue

Without this, Instagram can publish a shrunk or wrong-looking reel.

## Exact controls that matter

### Crop composer

- top-right action: `Next`
- bottom-left crop toggle: `Select crop`
- crop options:
  - `Original`
  - `1:1`
  - `9:16`
  - `16:9`

### New reel screen

- top-right action: `Share`
- caption field: `Write a caption...`

### Verification target

- profile: `https://www.instagram.com/void.to.victory/`
- reels tab: `https://www.instagram.com/void.to.victory/reels/`

## Mistakes to avoid

### 1. Do not assume old cookie files still match the target account

The old `cookies.txt` and related artifacts were tied to older session assumptions.

The working file for the current account is:

- `/home/meet/Downloads/cookies (2).txt`

### 2. Do not assume `_thecoco_club`

The uploader and verification must target:

- `void.to.victory`

### 3. Do not treat the saved-profile picker as “fully logged out”

If Instagram shows:

- `Log into Instagram`
- a row for `void.to.victory`

that is a recoverable state. Click the profile and continue. For this account, Instagram can still ask for the password after that step.

### 4. Do not trust “back on feed” as a success signal

Real verification requires:

- reels tab contains the new reel
- direct reel URL opens
- a real `<video>` is present
- the caption text is present

### 5. Do not rely only on hidden file input upload

For `void.to.victory`, chooser-first is more reliable than direct hidden-input upload.

## Verified result

The current successful local run produced:

- reel URL: `https://www.instagram.com/void.to.victory/reel/DWwuhSUDCeG/`
- profile target: `void.to.victory`
- `VIDEO_COUNT = 1`
- caption text present
- timestamp around `1m`

## Useful proof artifacts

- [after_profile_picker_home.png](/home/meet/projects/ai-video-gen/state/after_profile_picker_home.png)
- [after_crop_9_16.png](/home/meet/projects/ai-video-gen/state/after_crop_9_16.png)
- [after_caption.png](/home/meet/projects/ai-video-gen/state/after_caption.png)
- [after_share.png](/home/meet/projects/ai-video-gen/state/after_share.png)
- [verify_void_reels.png](/home/meet/projects/ai-video-gen/state/verify_void_reels.png)
- [verify_void_reel_detail.png](/home/meet/projects/ai-video-gen/state/verify_void_reel_detail.png)
