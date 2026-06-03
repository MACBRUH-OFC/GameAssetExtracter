import os, shutil, tempfile, traceback, logging
from ._bundle_loader import load_unity_environment, get_bundle_info
from ._asset_inventory_builder import build_asset_inventory
from ._asset_extractor_orchestrator import extract_single_asset_orchestrator
from ._archive_creator import create_archive

class BundleProcessor:
    def __init__(self, session_id, bundle_path, original_filename, session_upload_dir, app_config):
        self.session_id = session_id
        self.bundle_path = bundle_path
        self.original_filename = original_filename
        self.app_config = app_config
        self.processing_status = "initializing"
        self.metadata = {}
        self.progress = 0
        self.logger = logging.getLogger(f"session.{session_id}")
        self.objects = []

    def analyze_bundle(self):
        try:
            self.processing_status = "analyzing"
            self.env = load_unity_environment(self.bundle_path, self.logger)
            self.objects = list(self.env.objects)
            inventory = build_asset_inventory(self.objects, self.logger, False)
            self.metadata = {
                'bundle_info': {'filename': self.original_filename, 'object_count': len(self.objects)},
                'assets': inventory,
                'asset_classes': sorted(list(inventory.keys()))
            }
            self.processing_status = "completed"
            self.progress = 100
        except Exception as e:
            self.processing_status = "error"
            self.error_message = str(e)

    def extract_selected_assets(self, selected_indices):
        self.processing_status = "extracting"
        out_dir = tempfile.mkdtemp(dir=self.app_config['OUTPUT_FOLDER'])
        for idx in selected_indices:
            obj = self.objects[idx]
            extract_single_asset_orchestrator(obj, out_dir, self.logger, False)
        
        zip_path = create_archive(out_dir, self.original_filename, self.app_config['OUTPUT_FOLDER'], self.session_id, self.logger)
        shutil.rmtree(out_dir)
        self.processing_status = "completed"
        return zip_path
