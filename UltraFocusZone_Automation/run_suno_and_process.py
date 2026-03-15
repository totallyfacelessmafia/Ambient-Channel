from suno_automation import SunoAutomation
from loop_video_pipeline import VideoProcessor
import time

project = 'projects/processing/suno_test2'
loop_path = f"{project}/loop.mp4"
output_path = f"{project}/suno_test2_final_with_audio.mp4"
suno_dir = f"{project}/suno_outputs"
# Use a local Chrome profile folder so you can sign in (phone OTP) interactively once
profile_dir = f"{project}/chrome_profile"

sa = SunoAutomation(headless=False, download_dir=suno_dir, profile_dir=profile_dir)
try:
    if not sa.setup_driver():
        print('Browser setup failed')
    else:
        # Open Suno and give the user a chance to sign in interactively (phone OTP)
        sa.driver.get('https://suno.com/create')
        print('\nThe browser is open. Please sign in to Suno in the opened window (use phone OTP).')
        input('After you have signed in, press Enter here to continue and run generation...')
        try:
            sa.login()
        except Exception:
            pass
        out = sa.generate('warm, slow, ambient, electronic, instrumental, and deep')
        print('Suno generate returned:', out)
finally:
    sa.close()

# If audio was produced, attach it; otherwise proceed without audio
vp = VideoProcessor()
vp.loop_video(loop_path, output_path, audio_path=out if 'out' in locals() else None)
print('Done')
