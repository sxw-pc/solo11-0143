# 药品说明书版本适配采证台

离线采录虚构说明书版源、译文签核与销售包装证据，决定哪些资料版本和语种可关联某 SKU。只处理资料一致性，不提供用药建议，也不连接真实监管站点或药房。

## 运行

需要 Python 3.11+，在仓库根目录：

```powershell
python -m pip install . --no-build-isolation --no-deps
$env:PYTHONPATH=(Resolve-Path -LiteralPath src).Path
python -m leaflet demo --db data/demo.sqlite
python -m unittest discover -s tests -v
```

单独运行子系统：

```powershell
python -m leaflet import-source --db data/demo.sqlite --input fixtures/leaflet/source_good.json
python -m leaflet align --db data/demo.sqlite --input fixtures/leaflet/translation_good.json
python -m leaflet map-package --db data/demo.sqlite --input fixtures/leaflet/package_good.json
python -m leaflet report --db data/demo.sqlite --as-of 2026-11-17T10:00:00Z
python -m leaflet capture-rule-cards --db data/demo.sqlite --input fixtures/leaflet/rule_cards_good.json
```

版源账裁定发布主体授权、前驱链、同版号哈希冲突和定向撤回。语种账裁定关键章节覆盖、章节摘要绑定、独立签核和期限。包装账独立裁定剂型、mg/g 规格归一、销售市场、要求语种以及库存旧版资料替换顺序。三账各有入口和持久化表；报告按查询时点重算，原始 JSON 及 SHA-256 不覆盖。

## 可复现反例

在正常数据之后导入 `source_conflict.json`，同版号不同哈希让版源争议、语种阻断、包装冻结。单独用 `translation_missing.json` 导入 `en` 译文时，缺关键章节无法发布。`package_mismatch.json` 的缓释剂型即使强度等价也不能绑定普通片剂。测试还覆盖定向撤回只命中旧版、撤回前后按时点查询、旧签核不适用于新版、自签拒绝、市场不符和事件 ID 异载荷整批回滚。

初始化通过 `src/leaflet/store.py` 幂等建表；新增表结构应提供迁移，不得删除旧证据。未实现的边界见 `docs/initial-requirements.md`。

## 结构化章节采录

`capture-rule-cards` 采录虚构说明书关键章节里的结构化适用条件卡。`source` 卡与语种卡分别留存原始事件和摘要；目前只比较同键卡的条件 JSON 与结论是否逐字一致。已采到源卡时，`rule_card_verdict` 会参与现有 SKU 资料发布，语种卡缺失或条件不同就冻结资料绑定。运行 `rule_cards_good.json` 后再运行 `rule_cards_mismatch.json`，报告可见原来发布的 SKU 冻结和新的发布回执。这里的卡只用于资料版本一致性，不作临床判断。
