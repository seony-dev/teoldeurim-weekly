# tests/

이 프로젝트의 tracked 회귀 테스트 모음.

`local_output/*.py` 는 세션·디버깅용 임시 스크립트로 `.gitignore` 대상이라
저장소에 남지 않는다. 반면 이 폴더는 **git 추적** 되며, 다음 수정 때
장애 재발을 잡기 위해 반드시 다시 돌려봐야 하는 핵심 회귀만 담는다.

## 실행 방법

각 스크립트는 표준 라이브러리만 사용하는 standalone assertion script다.
프로젝트 루트에서 아래와 같이 실행:

```powershell
python tests/test_weekly_collect_drop.py
python tests/test_report_date_notice.py
```

혹은 전부 한 번에:

```bash
python tests/test_weekly_collect_drop.py && python tests/test_report_date_notice.py
```

각 스크립트는 실패 케이스가 있으면 `exit code 1`, 전부 통과면 `0` 을
반환한다. 외부 API·GitHub·이메일 발송을 전혀 건드리지 않으므로
로컬·CI 어디서든 안전하게 돌릴 수 있다.

## 목록

| 파일 | 무엇을 지키는가 |
|---|---|
| `test_weekly_collect_drop.py` | `weekly.collect()` 의 `drop` 집계가 신규 reason 코드 추가에도 `KeyError` 없이 안전. 2026-07-17 production 장애의 재발 방지 (Counter 사용) |
| `test_report_date_notice.py` | `REPORT_DATE` slot 날짜 override 와 실제 실행 시각 분리, `NOTICE` HTML escape · literal `\n` 정규화, `REISSUE` subject prefix 로직 |

## 추가 시 원칙

- **실제 실행 경로를 재현**하는 회귀를 넣는다. `hard_filter_reason()` 단독
  테스트만 있어서 `collect()` 안의 `drop[dropkey] += 1` KeyError 를
  놓쳤던 게 이 폴더가 생긴 이유다. 순수 유틸 함수 검증에 그치지 말고,
  실제 호출 흐름을 mock 으로 재현해서 통합적으로 검증한다.
- 외부 API 호출·이메일 발송·history 파일 변경은 절대 하지 않는다.
- 실패했을 때 무엇이 문제인지 이름만 봐도 알 수 있도록 chk() 라벨을 구체적으로.
