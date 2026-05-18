# WorldQuant BRAIN Official Metadata

This folder is the default home for official metadata synced by the user.

Run the sync command after logging in with your own WorldQuant BRAIN account and
starting the browser bridge:

```bash
python3 scripts/sync_worldquant_official.py --fields-only
```

The bootstrap path can run without this full sync by falling back to the public
starter metadata in `config/fields.yaml`.

