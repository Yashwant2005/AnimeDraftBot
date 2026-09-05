import asyncio
import logging
from config import OWNER_ID, OWNER_IDS
import database

class PerformanceManager:
    def __init__(self):
        # Total concurrent heavy tasks allowed (Battles/Draft processing)
        # 20 is a safe number for most VPS. Increase if you have strong CPU.
        self.total_concurrency = 20
        
        # Initial State: Equal Sharing
        self.main_semaphore = asyncio.Semaphore(self.total_concurrency)
        self.clone_semaphore = self.main_semaphore
        self.is_split_mode = False

    def update_allocation(self):
        """
        Recalculates resource allocation.
        If > 5 Clones: Main Bot gets 30% reserved slots. Clones share 70%.
        If <= 5 Clones: Everyone shares the total slots equally.
        """
        try:
            clones = database.get_all_clones()
            count = len(clones)
            
            if count > 5:
                # Calculate 30/70 Split
                main_slots = int(self.total_concurrency * 0.3)
                clone_slots = int(self.total_concurrency * 0.7)
                
                # Ensure minimum viability
                main_slots = max(2, main_slots)
                clone_slots = max(2, clone_slots)

                self.main_semaphore = asyncio.Semaphore(main_slots)
                self.clone_semaphore = asyncio.Semaphore(clone_slots)
                self.is_split_mode = True
                logging.info(f"⚡ [PERFORMANCE] High Load Mode Active: Main={main_slots} slots, Clones={clone_slots} slots")
            else:
                # Shared Pool
                self.main_semaphore = asyncio.Semaphore(self.total_concurrency)
                self.clone_semaphore = self.main_semaphore
                self.is_split_mode = False
                logging.info(f"⚡ [PERFORMANCE] Standard Mode: Shared {self.total_concurrency} slots")
        except Exception as e:
            logging.error(f"[PERFORMANCE] Error updating allocation: {e}")

    def get_semaphore(self, user_id):
        """Returns the appropriate semaphore based on user priority."""
        # Check if user is Main Owner or Admin
        if user_id == OWNER_ID or user_id in OWNER_IDS:
            return self.main_semaphore
        
        # Check if the context is the main bot (admin IDs)
        # If running on a clone, the clone owner is checked via user_id passed here
        return self.clone_semaphore

# Global Instance
manager = PerformanceManager()