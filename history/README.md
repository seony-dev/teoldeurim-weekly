# 주간 후보 아카이브

매주 금요일 자동 실행 결과가 `YYYY-MM-DD.json` 파일로 누적됩니다.

각 파일 구조:
```json
{
  "date_kst": "2026-04-25",
  "generated_at_utc": "2026-04-25T00:01:23Z",
  "config": { ... },
  "stats": {
    "lookback_days": 7,
    "total_collected": 156,
    "total_after_filter": 24
  },
  "candidates": [
    {
      "video_id": "...",
      "url": "https://www.youtube.com/shorts/...",
      "title": "...",
      "channel": "...",
      "published_at": "2026-04-22T...",
      "view_count": 2345678,
      "duration_seconds": 31,
      "default_lang": "ko"
    },
    ...
  ]
}
```

이 파일들로 시간이 지나면 트렌드 분석/소재 풀 구축 가능.
