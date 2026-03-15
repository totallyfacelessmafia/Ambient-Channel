"""
UltraFocusZone YouTube Automation - Main folder watcher
Monitors projects/queue/ for new project folders and processes them automatically.
"""
import os
import time
import shutil
import json
from pathlib import Path
from loop_video_pipeline import VideoProcessor


class FolderWatcher:
    def __init__(self, config_path="config.template.json"):
        """Initialize the folder watcher with configuration."""
        with open(config_path, 'r') as f:
            self.config = json.load(f)

        self.check_interval = self.config.get('check_interval_seconds', 5)

        # Setup folder paths
        self.base_path = Path(__file__).parent / "projects"
        self.queue_path = self.base_path / "queue"
        self.processing_path = self.base_path / "processing"
        self.completed_path = self.base_path / "completed"

        # Initialize video processor
        self.processor = VideoProcessor(config_path)

        print("UltraFocusZone Automation initialized")
        print(f"Queue folder: {self.queue_path}")
        print(f"Processing folder: {self.processing_path}")
        print(f"Completed folder: {self.completed_path}")

    def create_folders(self):
        """Create the necessary project folders if they don't exist."""
        self.queue_path.mkdir(parents=True, exist_ok=True)
        self.processing_path.mkdir(parents=True, exist_ok=True)
        self.completed_path.mkdir(parents=True, exist_ok=True)
        print("Project folders verified/created")

    def is_valid_project(self, folder_path):
        """
        Check if a folder contains the required files (background.png and loop.mp4).

        Args:
            folder_path: Path to the folder to check

        Returns:
            Boolean indicating if the folder is a valid project
        """
        folder = Path(folder_path)
        background = folder / "background.png"
        loop_video = folder / "loop.mp4"

        return background.exists() and loop_video.exists()

    def move_folder(self, source, destination):
        """
        Move a folder from source to destination.

        Args:
            source: Source folder path
            destination: Destination parent folder path
        """
        source_path = Path(source)
        dest_path = Path(destination) / source_path.name

        # If destination exists, remove it first
        if dest_path.exists():
            shutil.rmtree(dest_path)

        shutil.move(str(source_path), str(dest_path))
        return dest_path

    def process_project(self, project_folder):
        """
        Process a single project folder.

        Args:
            project_folder: Path to the project folder in the queue
        """
        project_path = Path(project_folder)
        project_name = project_path.name

        try:
            print(f"\n{'='*60}")
            print(f"Found new project: {project_name}")
            print(f"{'='*60}\n")

            # Move to processing folder
            print(f"Moving to processing: {project_name}")
            processing_folder = self.move_folder(project_path, self.processing_path)

            # Process the video and thumbnail
            result = self.processor.process_project(processing_folder)

            # Move to completed folder
            print(f"Moving to completed: {project_name}")
            completed_folder = self.move_folder(processing_folder, self.completed_path)

            print(f"\n{'='*60}")
            print(f"SUCCESS: {project_name}")
            print(f"Location: {completed_folder}")
            print(f"Video: {result['project_name']}_final.mp4")
            print(f"Thumbnail: {result['project_name']}_thumbnail.jpg")
            print(f"{'='*60}\n")

        except Exception as e:
            print(f"\n{'='*60}")
            print(f"ERROR processing {project_name}: {str(e)}")
            print(f"{'='*60}\n")

            # Move failed project back to queue
            if processing_folder.exists():
                print(f"Moving failed project back to queue: {project_name}")
                self.move_folder(processing_folder, self.queue_path)

    def scan_queue(self):
        """Scan the queue folder for new projects."""
        if not self.queue_path.exists():
            return

        # Get all folders in the queue
        folders = [f for f in self.queue_path.iterdir() if f.is_dir()]

        for folder in folders:
            # Check if it's a valid project
            if self.is_valid_project(folder):
                self.process_project(folder)
            else:
                print(f"Skipping invalid project (missing files): {folder.name}")

    def run(self):
        """Start the folder watcher main loop."""
        self.create_folders()

        print(f"\n{'='*60}")
        print("UltraFocusZone Automation is running!")
        print(f"{'='*60}")
        print(f"\nDrop project folders into: {self.queue_path}")
        print(f"Each folder must contain: background.png and loop.mp4")
        print(f"\nChecking every {self.check_interval} seconds...")
        print("Press Ctrl+C to stop\n")

        try:
            while True:
                self.scan_queue()
                time.sleep(self.check_interval)

        except KeyboardInterrupt:
            print("\n\nShutting down UltraFocusZone Automation...")
            print("Goodbye!")


if __name__ == "__main__":
    watcher = FolderWatcher()
    watcher.run()
