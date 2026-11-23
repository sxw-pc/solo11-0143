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

## 结构化章节采录与等义核证

`capture-rule-cards` 采录虚构说明书关键章节里的结构化适用条件卡，同一章节可挂多张卡。`scope` 是作用域表达式：原子谓词为 `market`、`form`、整数年龄区间 `age_years`（上界可为 `null`，表示开放年龄尾段，不预设最大年龄），复合表达式为显式的 `all`（并）、`any`（交）、`not`（非）与带名字的 `except` 例外引用；例外定义以同入口的 `rule-exception` 事件采录。市场和剂型只能取版源已授权集合里的值，未知枚举不会被并入现有集合，而是进入待核证据。

核证不再逐字比较 `scope_json` 与 `rule_key`。`src/leaflet/equivalence.py` 先把同语言、同章节、同 `rule_key` 的最新卡视为修订（输入次序和键名不影响结论），再把年龄轴按全部边界切成初等整数区间（含 `[lo, null]` 开放尾段），在版源授权市场 × 剂型 × 年龄的**整个适用域**上逐格比较源文与译文的动作函数：

- 全域动作一致 → `equivalent`，证据列出每个域格上的动作；
- 存在差异 → `different`，见证给出可重放的具体条件（市场、剂型、年龄段、两侧动作集合），译文缺卡只是动作函数为空，不再单列 `translated_rule_missing`；
- 未知枚举、未定义或循环例外、重复例外定义、同一侧同条件相反动作 → `pending` 待核，绝不按最后写入者取胜。

每次裁定都向不可变表 `rule_card_certification` 追加一行，记录输入卡摘要、适用域、差异见证和输入哈希；同输入同时点重复裁定靠哈希去重，重启可复现。旧 `rule_card_verdict` 仍是可覆盖的当前值表（沿用 `aligned`/`blocked` 词汇），报告按 `--as-of` 重算当前值，历史结论只从历史台账按时点读取，不能用今天的覆盖值冒充历史。

### 接收方口径：电子资料与印刷合同

- 面向单一市场的电子资料下载（`sku` 事件）只核证该 SKU 所在市场和剂型的投影域，投影通过即可发布；投影核证以 `receiver_type='sku'` 单独留痕，不会冒充全版译文等义。
- 同一实物包装随货进入多个授权市场时，用 `map-package` 入口采录 `print-contract` 事件（含合并市场集合）；印刷资料必须证明合并范围等义，任一市场存在差异或待核证据即冻结，并写入 `print_contract_binding` 与 `contract_publication_receipt`。局部通过不能覆盖印刷资料的冻结，两类接收方各自保留核证口径与依据。
- 源文卡撤回或版源不再可信时，后续裁定按 `edition.at` 重算为阻断，`package.decide` 同步更新 SKU 绑定；`publication_receipt` 继续只记发布状态变化，旧等义结论和依据保留在核证历史表里。

`rule_cards_split.json` 演示源文 0–5、6–12 两张排除卡与译文 0–12 一张卡等义；再采 `rule_cards_extra_market.json` 后，单市场 SKU 仍可发布而双市场印刷合同冻结。这里的卡只用于资料版本一致性，不作临床判断。
