"""User-facing explanations that the Security page and event log rely on."""

DISCLAIMER = (
    "Sentinel is a personal change-monitoring and file-integrity tool. It is NOT an "
    "antivirus, EDR or malware detector. A changed, new or deleted file is not "
    "automatically malicious - most changes are caused by normal software. "
    "Sentinel only observes and records: it never deletes, quarantines, modifies or "
    "uploads your files."
)

LIMITATIONS = (
    "Only the folders you add are watched; nothing else is scanned.",
    "File events come from the operating system. Very fast bursts of changes, network "
    "drives and some cloud-synced folders can be missed or reported late.",
    "Events are recorded only while Sentinel is running. Changes made while it was "
    "closed are only found by running 'Verify baseline'.",
    "Baseline checks compare SHA-256 hashes of the files YOU selected. They show that "
    "content changed, not why or by whom.",
    "Repeated events for the same file within about a second are merged, and recording "
    "is rate-limited (a summary event is stored when events are skipped).",
    "Sentinel's own data folder is ignored so it cannot report on itself.",
)

EVENT_EXPLANATIONS = {
    "file_created": "A new file appeared. New files are normal for most software; "
                    "review it only if you did not expect it.",
    "file_modified": "The file was changed. This is not by itself a sign of malware - "
                     "many programs rewrite files routinely.",
    "file_deleted": "The file was removed from the watched folder.",
    "file_moved": "The file was renamed or moved.",
    "dir_created": "A new folder appeared.",
    "dir_deleted": "A folder was removed.",
    "dir_moved": "A folder was renamed or moved.",
}
