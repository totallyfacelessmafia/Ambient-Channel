"""
Video looping and thumbnail generation pipeline for UltraFocusZone automation.
"""
import os
import json
from pathlib import Path
from moviepy.editor import VideoFileClip, concatenate_videoclips, AudioFileClip
from moviepy.audio.fx.all import audio_loop
from suno_automation import SunoAutomation
from PIL import Image


class VideoProcessor:
    def __init__(self, config_path="config.template.json"):
        """Initialize the video processor with configuration."""
        with open(config_path, 'r') as f:
            self.config = json.load(f)

        self.target_duration = self.config.get('video_duration_minutes', 60) * 60  # Convert to seconds
        self.thumbnail_width = self.config.get('thumbnail_width', 1280)
        self.thumbnail_height = self.config.get('thumbnail_height', 720)

    def loop_video(self, input_video_path, output_video_path, audio_path=None):
        """
        Loop a video to reach the target duration. Optionally attach audio.

        Args:
            input_video_path: Path to the input video file (loop.mp4)
            output_video_path: Path where the looped video will be saved
            audio_path: Optional path to an audio file to attach (will be looped to fit)
        """
        print(f"Loading video: {input_video_path}")
        clip = VideoFileClip(input_video_path)

        clip_duration = clip.duration
        num_loops = int(self.target_duration / clip_duration) + 1

        print(f"Video duration: {clip_duration}s, Target: {self.target_duration}s")
        print(f"Creating {num_loops} loops...")

        # Create list of clips to concatenate
        clips = [clip] * num_loops
        final_clip = concatenate_videoclips(clips)

        # Trim to exact duration
        final_clip = final_clip.subclip(0, self.target_duration)

        # If audio specified, load and loop it to match duration
        if audio_path and Path(audio_path).exists():
            try:
                audio = AudioFileClip(str(audio_path))
                if audio.duration < final_clip.duration:
                    audio = audio_loop(audio, duration=final_clip.duration)
                else:
                    audio = audio.subclip(0, final_clip.duration)
                final_clip = final_clip.set_audio(audio)
            except Exception as e:
                print(f"⚠️ Failed to attach audio: {e}")

        print(f"Writing looped video to: {output_video_path}")
        final_clip.write_videofile(
            output_video_path,
            codec='libx264',
            audio_codec='aac',
            fps=clip.fps
        )

        # Clean up
        clip.close()
        final_clip.close()

        print("Video looping complete!")
        return output_video_path

    def create_thumbnail(self, background_path, output_thumbnail_path):
        """
        Create a thumbnail from the background image.

        Args:
            background_path: Path to the background image (background.png)
            output_thumbnail_path: Path where the thumbnail will be saved
        """
        print(f"Creating thumbnail from: {background_path}")

        # Open and resize the background image
        img = Image.open(background_path)

        # Resize to YouTube thumbnail dimensions
        img_resized = img.resize((self.thumbnail_width, self.thumbnail_height), Image.Resampling.LANCZOS)

        # Save as JPEG for YouTube compatibility
        img_resized.save(output_thumbnail_path, 'JPEG', quality=95)

        print(f"Thumbnail saved to: {output_thumbnail_path}")
        return output_thumbnail_path

    def process_project(self, project_folder):
        """
        Process a complete project folder.

        Args:
            project_folder: Path to the project folder containing background.png and loop.mp4

        Returns:
            Dictionary with paths to the generated files
        """
        project_path = Path(project_folder)
        project_name = project_path.name

        # Input files
        background_path = project_path / "background.png"
        loop_video_path = project_path / "loop.mp4"

        # Check if required files exist
        if not background_path.exists():
            raise FileNotFoundError(f"background.png not found in {project_folder}")
        if not loop_video_path.exists():
            raise FileNotFoundError(f"loop.mp4 not found in {project_folder}")

        # Output files
        output_video_path = project_path / f"{project_name}_final.mp4"
        output_thumbnail_path = project_path / f"{project_name}_thumbnail.jpg"

        print(f"\n{'='*60}")
        print(f"Processing project: {project_name}")
        print(f"{'='*60}\n")

        # Generate Suno music first
        print("🔊 Generating music via Suno.ai...")
        suno_dir = project_path / "suno_outputs"
        suno_dir.mkdir(parents=True, exist_ok=True)

        # Number of generate clicks can be specified per-project via config.json -> "suno_clicks"
        repeats = 1
        cfg_file = project_path / "config.json"
        if cfg_file.exists():
            try:
                cfg = json.load(open(cfg_file))
                repeats = int(cfg.get('suno_clicks', repeats))
            except Exception:
                pass

        # Use the exact description requested by the user
        suno_prompt = "warm, slow, ambient, electronic, instrumental, and deep"
        sa = SunoAutomation(headless=True, download_dir=str(suno_dir))
        audio_files = []
        try:
            if not sa.setup_driver():
                print("⚠️ Suno browser setup failed; skipping music generation.")
            else:
                try:
                    sa.login()
                except Exception:
                    pass
                outs = sa.generate(suno_prompt, repeats=repeats, per_click=2)
                if outs:
                    # generate() returns a list
                    audio_files = outs
        finally:
            sa.close()

        audio_file = audio_files[0] if audio_files else None

        # Process video and attach audio if present
        self.loop_video(str(loop_video_path), str(output_video_path), audio_path=audio_file)

        # Create thumbnail
        self.create_thumbnail(str(background_path), str(output_thumbnail_path))

        print(f"\n{'='*60}")
        print(f"Project complete: {project_name}")
        print(f"{'='*60}\n")

        return {
            'video': str(output_video_path),
            'thumbnail': str(output_thumbnail_path),
            'project_name': project_name
        }


if __name__ == "__main__":
    # Example usage
    processor = VideoProcessor()

    # Example: process a project folder
    # processor.process_project("projects/queue/my_project")
    print("VideoProcessor initialized. Use process_project() to process a folder.")
