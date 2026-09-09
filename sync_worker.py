"""Run once from cron every five minutes; respects each user's opt-in."""
from app import app, db, sync_gmail
with app.app_context():
    ids=[r[0] for r in db().execute('SELECT u.id FROM users u JOIN gmail g ON g.user_id=u.id WHERE u.auto_sync=1')]
    for uid in ids:
        try:
            n=sync_gmail(uid)
            print(f'User {uid}: {n} new drafts')
        except Exception as exc:
            # Never log email bodies, credentials, or provider responses.
            print(f'User {uid}: sync failed ({type(exc).__name__})')
