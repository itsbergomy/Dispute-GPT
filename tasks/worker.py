"""
Huey task queue worker with SQLite backend.
Run: huey_consumer tasks.worker.huey
"""

from huey import SqliteHuey

huey = SqliteHuey(filename='instance/task_queue.db')
