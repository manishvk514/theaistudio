"""Forensic disk engine: reads FAT12/16/32, exFAT and NTFS directly (deleted file records with
names, dates and exact cluster layout) and carves only unallocated space, validating every file
by its own internal structure. Read-only."""
