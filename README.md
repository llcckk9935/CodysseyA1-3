# 나를 요리해줘

냉장고에 남은 핵심 재료로 요리 초보자를 위한 간단한 한 끼 레시피 2개를 추천하는 AI 웹 서비스입니다.

> 배포 URL: Vercel 배포 후 여기에 주소를 추가하세요.

## 주요 기능

- 핵심 재료와 인원수를 입력하면 AI가 모든 핵심 재료를 포함한 레시피 2개 제안
- 식단 제한·알레르기 칩 및 직접 입력, 충돌·모호 입력 안내
- 레시피당 추가 재료 최대 2개, 최대 5단계 조리 순서
- 개별 레시피 저장·상세 보기·삭제 (브라우저 LocalStorage)
- 다크 모드 저장, 8초 로딩 안내·25초 화면 대기 종료, 오류별 안내

## 기술 스택

- Frontend: HTML, CSS, Vanilla JavaScript
- Backend: Vercel Serverless Functions (Python)
- AI: OpenAI Responses API, `gpt-5-mini`, Structured Outputs
- Deploy: Vercel

## 로컬 실행

정적 화면만 확인하려면 `index.html`을 브라우저로 열 수 있습니다. API까지 테스트하려면 Vercel CLI를 사용하세요.

```bash
npm install -g vercel
vercel dev
```

`.env` 파일을 만들고 실제 API 키를 설정합니다. 이 파일은 Git에 올리지 마세요.

```env
OPENAI_API_KEY=실제_키를_여기에_입력
# 교육장 전용 Base URL을 받은 경우에만 설정
OPENAI_BASE_URL=https://교육장에서_받은_Base_URL
```

## 배포

1. GitHub에 `CodysseyA1-3` 공개 저장소를 만들고 코드를 push합니다.
2. Vercel에서 **Continue with GitHub**으로 가입한 뒤 저장소를 Import합니다.
3. Project Settings → Environment Variables에 `OPENAI_API_KEY`를 추가합니다. 교육장 전용 Public API Base URL을 받은 경우 `OPENAI_BASE_URL`도 추가합니다.
4. Deploy 후 README의 배포 URL을 실제 주소로 교체합니다.

## 보안과 개인정보

- 키는 서버 환경 변수로만 사용하며 브라우저 코드와 Git에 포함하지 않습니다.
- `.env.example`에는 변수명 예시만 있습니다.
- 재료·식단·알레르기 정보는 추천 생성 목적으로 AI API에 전송되고 서버에 별도 저장하지 않습니다.
- 저장 레시피와 테마는 로그인 계정이 아닌 해당 브라우저에 보관됩니다.
- 알레르기·식단 정보는 AI가 완전히 보장할 수 없습니다. 성분표와 교차오염 여부를 직접 확인하세요.

## 프로젝트 구조

```text
.
├── api/recommend.py       # OpenAI를 호출하는 Python Serverless Function
├── css/style.css          # 반응형·다크 모드 스타일
├── js/app.js              # 입력, fetch, 저장, 화면 처리
├── docs/service-plan.md   # 서비스 기획서
├── index.html
├── requirements.txt
└── .env.example
```
