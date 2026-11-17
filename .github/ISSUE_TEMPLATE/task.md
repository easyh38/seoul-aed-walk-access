---
name: 작업
about: 기능 추가, 버그 수정, 오류 기록 등 작업 하나를 설명한다
title: ""
labels: ""
assignees: ""
---

## 배경
왜 필요한가 (관련 PRD 기능 ID: 예 P-02)

## 현재 동작
예: 운영시간 '2400'을 넣으면 ValueError가 난다

## 기대 동작
예: '2400'은 자정(1440분)으로 해석된다

## 완료 기준
- [ ] pytest tests/test_hours.py 통과
- [ ] cleaning_report.csv에 해석 실패 0건
