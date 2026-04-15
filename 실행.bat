@echo off
cd /d "%~dp0"
echo [1/2] npm 패키지 설치 중...
"C:\Program Files
odejs
pm.cmd" install --prefer-offline -q 2>/dev/null || "C:\Program Files
odejs
pm.cmd" install -q
echo [2/2] 키워드 필터 검색기 시작...
"C:\Program Files
odejs
pm.cmd" start
