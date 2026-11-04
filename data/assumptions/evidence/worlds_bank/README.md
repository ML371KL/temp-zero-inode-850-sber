# worlds_bank — банковская надстройка миров (A-M, MODEL §3.1, прил. B)

`build_overlay.py` собирает `data/assumptions/worlds_bank.json` из черновика надстройки этапа 1
(`inputs/bank_overlay_draft.yaml` — копия черновика, абсолютные пути заменены относительными; `--sync` обновляет копию
из `$BANK_HANDOFF_DIR`, `--check` — сверка). Хэш файла вписывает в книгу `build_assumptions.py`
(`worlds.overlay.sha256`); ядро сверяет файл с этим хэшем.

Правила: середины диапазонов ЦБ → доли; N — базовый сценарий Банка России: с пересборки миров 1.7 (29.10.2026) —
среднесрочный прогноз 23.10.2026 (`forecast_261023.pdf`, с. 1, sha256 0e007c6d…; те же числа — табл. 3.2 итоговой ОНДКП
28.10.2026, PDF-с. 73), до неё — табл. 3.2 проекта ОНДКП 30.09.2026 (sha256 3c3270ef…); «прочая розница» 2026–2027 — прогноз ЦБ по балансам («Банковский сектор. II кв. 2026», PDF-с. 20),
2028–2029 — (население − w·ИЖК)/(1 − w), w = 0,5806; H и M — реальный рост базы при инфляции своего мира
(g_W = (1 + g_N)(1 + π_W)/(1 + π_N) − 1, π — средний ИПЦ мира за год по записи миров); `funds_growth.retail =
funds_growth.corporate` = М2; годы 2026–2029, дальше ядро сводит рост к g_T мира к `volumes.lt_from` (2031).
Самопроверка: черновик собран на той же записи миров (1.7 — sha256 1e905cb7…), H и M — пересчёт N по инфляции мира
(±0,011 п.п.). Дата и текст рецепта надстройки (`source.as_of`, `recipe`) с 1.7 едут с черновиком (`meta.overlay`); черновик
без блока даёт прежние константы — надстройка 30.09.2026 (sha256 6fb82a41…) воспроизводится бит в бит. Черновик 1.7 собран
`bank_overlay_build_1_7.py` (канон миров 1.7, `worlds-cbr-2026-10-23/impl/canon/bank/`).

Надстройка 1.7 (sha256 8611f015…; «было» — надстройка 30.09.2026 в скобках у изменившихся чисел — в журнале канона):

| Мир | Кредиты ЮЛ 2026 / 27 / 28 / 29 | ИЖК | Прочая розница | Средства (М2) |
|---|---|---|---|---|
| N | <!--~bank:worlds.N.credit_growth.corporate.2026 p1-->10,5<!--~--> / <!--~bank:worlds.N.credit_growth.corporate.2027 p1-->8,5<!--~--> / <!--~bank:worlds.N.credit_growth.corporate.2028 p1-->10,5<!--~--> / <!--~bank:worlds.N.credit_growth.corporate.2029 p1-->10,5<!--~--> % | <!--~bank:worlds.N.credit_growth.mortgage.2026 p1-->7,5<!--~--> / <!--~bank:worlds.N.credit_growth.mortgage.2027 p1-->7,5<!--~--> / <!--~bank:worlds.N.credit_growth.mortgage.2028 p1-->12,5<!--~--> / <!--~bank:worlds.N.credit_growth.mortgage.2029 p1-->12,5<!--~--> | <!--~bank:worlds.N.credit_growth.retail_other.2026 p1-->6,0<!--~--> / <!--~bank:worlds.N.credit_growth.retail_other.2027 p1-->5,5<!--~--> / <!--~bank:worlds.N.credit_growth.retail_other.2028 p1-->7,7<!--~--> / <!--~bank:worlds.N.credit_growth.retail_other.2029 p1-->7,7<!--~--> | <!--~bank:worlds.N.funds_growth.retail.2026 p1-->11,5<!--~--> / <!--~bank:worlds.N.funds_growth.retail.2027 p1-->7,5<!--~--> / <!--~bank:worlds.N.funds_growth.retail.2028 p1-->9,5<!--~--> / <!--~bank:worlds.N.funds_growth.retail.2029 p1-->9,5<!--~--> |
| H | <!--~bank:worlds.H.credit_growth.corporate.2026 p1-->10,5<!--~--> / <!--~bank:worlds.H.credit_growth.corporate.2027 p1-->10,2<!--~--> / <!--~bank:worlds.H.credit_growth.corporate.2028 p1-->13,4<!--~--> / <!--~bank:worlds.H.credit_growth.corporate.2029 p1-->13,2<!--~--> | <!--~bank:worlds.H.credit_growth.mortgage.2026 p1-->7,5<!--~--> / <!--~bank:worlds.H.credit_growth.mortgage.2027 p1-->9,2<!--~--> / <!--~bank:worlds.H.credit_growth.mortgage.2028 p1-->15,4<!--~--> / <!--~bank:worlds.H.credit_growth.mortgage.2029 p1-->15,3<!--~--> | <!--~bank:worlds.H.credit_growth.retail_other.2026 p1-->6,0<!--~--> / <!--~bank:worlds.H.credit_growth.retail_other.2027 p1-->7,2<!--~--> / <!--~bank:worlds.H.credit_growth.retail_other.2028 p1-->10,5<!--~--> / <!--~bank:worlds.H.credit_growth.retail_other.2029 p1-->10,4<!--~--> | <!--~bank:worlds.H.funds_growth.retail.2026 p1-->11,5<!--~--> / <!--~bank:worlds.H.funds_growth.retail.2027 p1-->9,2<!--~--> / <!--~bank:worlds.H.funds_growth.retail.2028 p1-->12,3<!--~--> / <!--~bank:worlds.H.funds_growth.retail.2029 p1-->12,2<!--~--> |
| M | <!--~bank:worlds.M.credit_growth.corporate.2026 p1-->10,5<!--~--> / <!--~bank:worlds.M.credit_growth.corporate.2027 p1-->8,5<!--~--> / <!--~bank:worlds.M.credit_growth.corporate.2028 p1-->13,7<!--~--> / <!--~bank:worlds.M.credit_growth.corporate.2029 p1-->16,9<!--~--> | <!--~bank:worlds.M.credit_growth.mortgage.2026 p1-->7,5<!--~--> / <!--~bank:worlds.M.credit_growth.mortgage.2027 p1-->7,5<!--~--> / <!--~bank:worlds.M.credit_growth.mortgage.2028 p1-->15,8<!--~--> / <!--~bank:worlds.M.credit_growth.mortgage.2029 p1-->19,0<!--~--> | <!--~bank:worlds.M.credit_growth.retail_other.2026 p1-->6,0<!--~--> / <!--~bank:worlds.M.credit_growth.retail_other.2027 p1-->5,5<!--~--> / <!--~bank:worlds.M.credit_growth.retail_other.2028 p1-->10,9<!--~--> / <!--~bank:worlds.M.credit_growth.retail_other.2029 p1-->14,0<!--~--> | <!--~bank:worlds.M.funds_growth.retail.2026 p1-->11,5<!--~--> / <!--~bank:worlds.M.funds_growth.retail.2027 p1-->7,5<!--~--> / <!--~bank:worlds.M.funds_growth.retail.2028 p1-->12,7<!--~--> / <!--~bank:worlds.M.funds_growth.retail.2029 p1-->15,9<!--~--> |

Год якоря для кредитов перекрывает гайденс (`volumes.guidance_growth`); рисковый сценарий ЦБ (табл. 3.5) в миры не
входит — он в режиме «кризис» (`regimes.crisis.loan_growth_override`) и в гейте `m_crisis_vs_cbr`.
