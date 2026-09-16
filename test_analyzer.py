from signal_analyzer import analyze_messages


messages = [
    {
        "message_id": 28538,
        "date": "2026-09-09 15:16:32",
        "text": "GOLD BUY NOW"
    },
    {
        "message_id": 28539,
        "date": "2026-09-09 15:16:32",
        "text": "PAM"
    },
    {
        "message_id": 28540,
        "date": "2026-09-09 15:16:32",
        "text": "ZONE: 4391-4388\nCUTLOSS: 4386\nTP:OPEN/1:1/1:3"
    }
]


if __name__ == "__main__":
    # This is an explicit integration smoke test, not a unit test. It may make
    # a paid/network request and therefore must never run during discovery.
    result = analyze_messages(messages)
    print()
    print("=" * 70)
    print("🎯 SIGNAL SNIPER AI RESULT")
    print("=" * 70)
    print(result.model_dump_json(indent=2))
    print("=" * 70)
