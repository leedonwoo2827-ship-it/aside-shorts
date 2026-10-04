# aside-shorts — 딸깍 교과 쇼츠 (Claude 모션그래픽 + SuperTonic3 나레이션)

교과 원고(.docx) → 관점별 **음성 나레이션 쇼츠(1080×1920, 30~40초)** → YouTube Studio 자동 업로드·예약.

레퍼런스: 「편집 프로그램 없이 모션그래픽 만드는 법 (클로드 오퍼스 5.5)」 5:40~ 의 방식을 자동화했다.
> 주제 하나로 15초 남짓 대사 → AI 나레이션 → 필요한 그림 6장 → 대사·음성·그림·스타일 레퍼런스를
> Claude 에게 주고 **목소리에 맞춰** 움직이는 모션그래픽을 만든다. 영상 생성 모델은 쓰지 않는다.
>
> 여기서는 **Claude CLI(구독 OAuth 로그인) 하나**로 대본·그림·모션을 모두 만든다. 그림은 Claude 가 SVG 일러스트로 직접 그린다
> (ChatGPT/Codex·API 키 불필요). 목소리만 로컬 SuperTonic3.

형태는 `D:\00work\261003-aside-threade-chatgptoa`(aside-threads)를 그대로 따랐다:
번호 단계(s1…s5) · jobs 폴더 · overrides(사람 편집이 이김) · 캐시로 이어하기 · 오른쪽 사이드 패널 · 실제 Chrome(CDP) 업로드 · 예약 큐.

## 파이프라인

| 단계 | 누가 | 하는 일 | 결과 |
|---|---|---|---|
| s0-rewrite | Claude + 코드 검증 | **교과서 원문 → 원문과 전혀 다른 해설 글**: 「(1) 시간적 관점」 소제목 + 해설 3~4문단(소제목 하나 = 쇼츠 한 편). 개념어 유지, 사례 교체, 문장은 전부 새로. 코드가 원문과 15자 넘는 겹침·문단 유사도(>0.5)·보존어를 검사, 걸리면 다시 | `02_rewrite/*.docx` · `sections.json` · `검증.json` |
| s1-script | Claude (`claude -p`, Opus) | **개작본만** 읽어 관점별 쇼츠 대본(후크 2줄 · 문장 6~8 · 연출 메모 · 그림 목록 · 유튜브 제목/설명/해시태그) | `03_script/<id>.json` |
| s2-tts | **SuperTonic3**(내장, ONNX) | 문장별 음성 → 한 줄로 잇고 **문장별 시작·끝 시각** 기록 | `04_bundle/<id>/audio/` |
| s3-images | Claude (SVG 일러스트) | 스티커(투명)·배경·도해를 SVG 로 그려 PNG 로 변환. 패널에서 직접 만든 그림으로 교체 가능 | `04_bundle/<id>/images/` |
| s4-motion | Claude (Opus, 그림을 직접 열어 봄) | 대사·타이밍·그림·스타일 가이드 → HTML+GSAP 장면. 브라우저로 점검해 오류가 있으면 Claude 가 다시 고침 | `04_bundle/<id>/motion/` |
| s5-render | Playwright + ffmpeg | 멈춘 타임라인을 프레임마다 찍어 mp4 + 음성 | `04_bundle/<id>/out/<id>.mp4` |
| post | 실제 Chrome(CDP) | Studio 업로드 → 제목·설명 → 아동용 아님 → 공개/예약 | `state.json` |

id 는 `<장>-<절>-<NN>` (예: `1-2-04` = 1장 2절의 4번째 관점).

## 폴더 구조

```
jobs/<작업>/
  job.json · state.json
  1-2_통합적 관점의 필요성과 적용/      ← 단원(절) 하나 = 폴더 하나
    01_raw/      원본 docx (보관만 — 절대 고치지 않음)
    02_rewrite/  안전 개작 docx + 검증.json                       ← 0 안전 개작
    03_script/   1-2-01.json … (관점별 대본) · *.edit.json(사람 수정)   ← 1 대본
    04_bundle/
      1-2-01/    audio/ (2 목소리) · images/ (3 그림) · motion/ (4 모션) · out/1-2-01.mp4 (5 영상)
      1-2-02/ …
```
대본(1)부터는 **02_rewrite 만** 재료로 쓴다 — 원본은 쇼츠에 들어가지 않는다.
※ 0 안전 개작은 원문과의 표현 겹침을 줄이는 자동 장치이지 법적 판단이 아니다. 「새 작업·원고 → 비교」로 원본·개작본을 나란히 보고 사람이 최종 확인한다.

## 처음 한 번

```bat
setup.bat            :: venv · 패키지 · Chromium · 글꼴/GSAP · 점검
claude auth login    :: Claude 구독 OAuth 로그인 (대본·그림·모션)  ※ 패널의 「Claude 로그인」으로도 됨
run.bat              :: 제작 대시보드(넓은 웹앱 창)
```
## 화면 두 개

- **제작 대시보드** (`run.bat` → 넓은 창, http://127.0.0.1:5293/) — lecture-composer 와 같은 형태
  - 대시보드: 실행 옵션 · 단계 카드(1 대본 → 5 영상, 딸깍) · 쇼츠별 진행표(행마다 딸깍/모션 다시/영상 다시/보기) · 아래 실시간 로그
  - 결과 보기: 영상/모션 미리보기 · 그림 6장(교체) · 대본 편집(+모션 수정 요청) · 유튜브 제목·설명
  - 새 작업·원고: 폴더/파일 고르기 · .docx 끌어다 놓기 · 원고 목록
  - 설정: 이 작업(job.json) · 이 PC 전체 설정(aside.config.local.json — 회사 PC 의 SuperTonic3 폴더 등)
  - 계정·올리기: Claude 로그인 상태 · SuperTonic3 · YouTube 계정 · 「업로드 패널 열기」 · 예약 기록
- **업로드 패널** (대시보드 → 계정·올리기 → 🚀 업로드 패널 열기, http://127.0.0.1:5293/panel)
  - 오른쪽 좁은 창 + 왼쪽 YouTube Studio Chrome. ＋계정(예: `dekman`, 표시 `@dekmanfactory`) → 로그인 → Google 로그인·채널 선택(처음 한 번)
  - 쇼츠 → 미리 채워 보기 → 지금 올리기/예약

SuperTonic3 는 **이 프로젝트에 내장**되어 있다(`aside_shorts/supertonic/`). setup.bat 이 모델·목소리 10종을
HuggingFace `Supertone/supertonic-3` 에서 `assets/supertonic/` 로 받는다(약 380MB, 처음 한 번). 다른 프로그램·폴더가 필요 없다.
**다른 TTS 로 넘어가지 않는다.** 발음사전은 `aside_shorts/supertonic/pronunciation_map.yaml` (AI→에이아이 등).

## 매번 (CLI 로도 똑같이)

```bat
run.bat new tonghap1 --from "D:\00work\260711-고틍학교통합사회1\__assetes" --title "고등학교 통합사회1"
run.bat s0-rewrite --job tonghap1                :: 원본 → 안전 개작본
run.bat s1-script --job tonghap1                 :: 대본만 먼저 보고 싶을 때
run.bat make --job tonghap1 --only 1-2-04        :: 한 편 끝까지
run.bat make --job tonghap1                      :: 전부 (이미 된 건 건너뜀)
run.bat post --job tonghap1 --only 1-2-04 --dry-run          :: Studio 창에 채워만 보기
run.bat post --job tonghap1 --only 1-2-04 --visibility private
run.bat post --job tonghap1 --only 1-2-04 --at "2026-10-05 19:00"   :: YouTube 예약 공개
run.bat plan --job tonghap1 --apply              :: 남은 것 슬롯(12:00·19:00)에 자동 예약
```
- `--from` 에 260711 형식 폴더를 주면 `01_raw` → 원본(01_raw), `02_` → 개작본(02_rewrite) 으로 나눠 가져온다. 그냥 파일은 원본으로(이미 개작본이면 `--rewritten`).
- 고치기: 패널에서 문장·읽는 소리·연출 메모·**모션 수정 요청**을 고치고 「저장 + 다시 만들기」 → 바뀐 단계만 다시.
  (`overrides/<id>.json` 에 저장되어 Claude 가 다시 써도 사람 편집이 이긴다.)
- 모션 미리보기: `jobs/<job>/motion/<id>/index.html` 을 브라우저(패널 링크)로 열고 화면을 클릭하면 음성과 함께 재생.

## 유튜브 올릴 문구 (@dekmanfactory 형식으로 통일)

1 대본 직후 `ytmeta` 가 쓰고, `youtube.meta()` 한 곳에서 조립한다(자동 업로드·패널 「복사」 모두 같은 결과).
```
제목   세계가 석유로 매일 싸우는 진짜 이유 #shorts #자원갈등 #공간적관점 … #쇼츠      (100자 안에서 뒤 태그부터 뺌)
설명   지구가 자원 전쟁으로 끊임없이 갈등하는 진짜 이유!
       원유 매장지와 소비지, 이동 경로의 공간적 상호작용이 세계 경제의 판도를 바꿉니다.
       #자원갈등 #공간적관점 #석유전쟁 #세계경제 #shorts

       (2) 공간적 관점
       개념 설명 문단들 = 그 편의 개작본(02) 소제목 글 — 이미 원문과 다른지 검사됨
태그   Studio 「태그」 칸 10~14개       고정 댓글  시청자에게 묻는 질문 한 줄
```
- 자동 업로드는 태그 칸까지 채우고, **바로 공개**면 고정 댓글을 달아 고정한다(예약·비공개면 공개 뒤 패널 「복사」로).
  태그·고정 댓글은 Studio 화면이 바뀌어 실패해도 업로드는 계속한다(`logs/post/` 스크린샷).
- 문구만 다시: 결과 보기 → 유튜브 올릴 문구 → 「유튜브 문구 다시 쓰기」 (`run.bat yt-meta --job J --only id --force`).

## 밤샘·며칠 무인 제작

- **딸깍 (2→5)**: 대본이 있는 쇼츠를 **한 편씩** 목소리 → 그림 → 모션 → 영상. 진행표에서 체크한 것만 돌릴 수도 있다(「선택한 쇼츠 딸깍」).
- **밤샘 (0→5)**: 원고를 많이 넣은 날 밤에. 단원마다 0 개작 → 1 대본, 그다음 쇼츠마다 2 → 5. (`run.bat all --job J`)
- **한 편이 실패해도 다음 편으로** 넘어간다. 끝에 실패 목록을 보여 주고, 같은 버튼을 다시 누르면 이미 된 건 건너뛰고 남은 것만 한다.
- **Claude 사용 한도(5시간·주간)에 걸리면 기다렸다 이어 간다** — 리셋 시각을 알면 그 시각까지(1시간마다 확인), 모르면 30분마다 확인.
  최대 72시간. 설정 탭 `claude.limit_wait_hours` · `limit_poll_min` (0 = 바로 멈춤). 확인은 haiku 한 마디라 사용량이 거의 들지 않는다.
- 작업하는 동안 PC 절전을 막는다. **run.bat 검은 창과 대시보드는 켜 둘 것**(최소화는 괜찮음).

## 스타일

`templates/styles/<이름>/` — `style.json`(그림 스타일 문구) · `guide.md`(Claude 용 모션 설명서) · `frame.css`(후크·자막 틀)
- 스타일마다 `style.json` 의 `svg` 칸이 그림(SVG) 팔레트·질감 지시다.
- `vox-retro` (기본): 크림 종이 · 하프톤 사진 스티커 · 노란 형광펜 · 빨간 손그림 · 청사진 도해 · 타임라인 (QWERTY 예시)
- `collage-paper`: 수채화 배경 · 흰 테두리 스티커 · 마스킹테이프 손글씨 · 찢어진 종이 전환 (고양이 예시)

새 스타일 = 폴더 하나 복사해서 세 파일만 고치면 된다. job 별 선택: `new … --style collage-paper` 또는 job.json `shorts.style`.

## 모션 장면 규칙 (s4 ↔ 런타임 계약)

`templates/base/runtime.js` 가 `SHORT`·`TL`(멈춘 GSAP 타임라인)·`at(i)`·`end(i)`·`IMG(key)`·`rand(seed)` 를 준다.
Claude 는 `<img data-img="img3" class="sticker">` 와 `TL.from(…, at(2))` 만 쓴다. 상단 후크·하단 자막은 런타임이 그린다.
렌더러는 프레임마다 `__seek(t)` 로 시각을 찍으므로 setTimeout·CSS 애니메이션·Math.random 은 금지(점검에서 걸러 Claude 가 고침).

## 설정 (`aside.config.json`, PC 별 덮어쓰기 `aside.config.local.json`)
- `claude.script_model/image_model/motion_model` (opus) · `claude.see_images` (Claude 가 그림을 직접 열어 보고 배치)
- `shorts.target_seconds` (35) · `lines_min/max` · `images_min/max` · `style`
- `tts.voice` (F1~F5 / M1~M5) · `tts.speed` (1.1)
- `youtube.native_schedule` (true: Studio 예약 공개 — PC 꺼져도 됨) · `slots` · `visibility`

## Studio 화면이 바뀌면
`aside_shorts/youtube.py` 의 `SEL` 표만 고친다. `run.bat probe` 가 업로드 창 구조·스크린샷을 `logs/probe/` 에 남긴다.
업로드 단계별 스크린샷은 `logs/post/`, 기술 로그는 `logs/detail.log`.
