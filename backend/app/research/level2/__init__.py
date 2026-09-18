"""Level 2 交易層（FRS 2026-09-18 v1.0）。

純函式核心：costs（成本/tick/漲跌停）→ engine（委託執行/帳戶狀態/重放）→
policy（Baseline v0）→ simulate（日迴圈）。無 I/O——回測、live paper、
Phase 2 RL 環境三者共用同一份核心（FRS §7/§8）。
"""
