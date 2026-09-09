"""Run once from cron every five minutes; respects each user's opt-in."""
from app import app, sync_gmail, auto_sync_uids
with app.app_context():
    for uid in auto_sync_uids():
        try:
            n=sync_gmail(uid)
            print(f'User {uid}: {n} new drafts')
        except Exception as exc:
            # Never log email bodies, credentials, or provider responses.
            print(f'User {uid}: sync failed ({type(exc).__name__})')