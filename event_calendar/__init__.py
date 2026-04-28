"""Event calendar module: drives both pre-earnings and ECTS workflows.

Two CronJob entry points share this package:
- calendar_sync_main: scrapes earnings call times and seeds the GCS task registry.
- task_dispatcher_main: reads the registry and publishes Pub/Sub triggers.
"""
