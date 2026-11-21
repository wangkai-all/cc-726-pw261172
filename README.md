# MARC21 Ingest Station

网页上传 ISO 2709 / MARC21 文件，按记录检查字段、指示符、子字段与原始字节，
在线编辑字段映射并即时查看单条记录如何展开，最后导出带字节位置与原记录编号的
可查询数据集。

## 设计要点（与需求逐条对应）

### 1. 严格按字节切分，绝不按解码后的字符位置切文件

`backend/app/marc/iso2709.py` 只信任 Leader 与目录项中的字节数值：

- Leader 00–04 的记录长度、12–16 的数据区基地址（base address of data）；
- 每条 12 字节目录项 = `tag(3) + field_length(4) + starting_position(5)`，
  起点相对于基地址；
- 字段长度包含字段终止符 `0x1E`，记录以 `0x1D` 结束，目录以 `0x1E` 结束。

多字节 UTF-8 字符（一个汉字 3 字节）不会影响任何偏移计算，测试
`test_splitting_uses_bytes_not_char_positions` 固定了这一行为。

### 2. 控制字段与数据字段分开；重复有序、绝不覆盖

- `00x` 标签走控制字段路径（无子字段分隔符），其余按数据字段解析两个指示符与
  `0x1F` 分隔的子字段；
- 同一标签的每次出现、同一子字段代码的每次出现都是独立对象，带
  `field_order` / `subfield_order` 与绝对字节偏移；
- 映射引擎（`mapping.py`）把重复值展开为**有序数组**，重复字段可选建模为
  子表（外键 `record_no` 关联主记录行），书名多段值不会被塞进同一个键互相覆盖。

### 3. UTF-8 与 MARC-8；无法解释的字节保留而不是替换成问号

`backend/app/marc/charset.py` 依据 Leader 第 9 位选择编码（`a` = UTF-8，
否则 MARC-8）：

- MARC-8 复用 pymarc 的 `marc8_mapping` 码表（ANSEL、各 G0/G1 字符集、EACC
  多字节），自行实现支持 2/3/4 字节转义序列的状态机；
- ANSEL 组合变音符号按 MARC-8 规则（前置于基字符）转换为 Unicode 组合字符，
  并记录其名称与字节偏移；
- 任何无法解释的字节（非法 UTF-8 序列、未映射 MARC-8 字节、截断的转义序列、
  未知字符集指定符）原样保留在私有区标记 `..` 中，标记内是原始
  十六进制，同时产生结构化 issue。代码中没有任何 `?` 替换路径；
- 前端把标记渲染为醒目的原始字节徽章，而不是看不见的替换字符。

### 4. 坏记录隔离 + 可验证的重新定位

- 坏目录、缺少终止符、记录长度不符等问题只把**该记录**标记为 `invalid`，
  原始字节完整保留；
- 之后逐个字节扫描下一个“5 位数字长度 + 声明终止符位置确有 `0x1D` +
  基地址前确有目录终止符”的可信记录起点，后续可定位记录照常解析；
- 找不到任何可信后续记录时，产生 `fatal / cannot_relocate` 文件级问题并
  明确停止，绝不臆造记录。

### 5. 导出可查询且保留溯源

`backend/app/marc/exporter.py` 支持三种格式，所有行都带原记录编号
（`record_no`）与文件内字节偏移：

- **SQLite**：`records` 主表 + 每个重复字段一张子表（外键关联）+ `export_meta`
  （映射方案、跳过记录清单），可直接 SQL 查询；
- **JSON Lines**：每行一条主记录，内嵌子表行与 `_source` 溯源；
- **CSV zip**：`main.csv` + 每子表一个 CSV，数组/对象单元格用 JSON 编码。

## 目录结构

```
backend/
  app/
    marc/
      iso2709.py     # 字节级 ISO 2709 解析、错误隔离与重定位
      charset.py     # UTF-8 / MARC-8 解码、坏字节保留、组合字符追踪
      mapping.py     # 映射方案、主表/子表展开、校验
      exporter.py    # SQLite / JSONL / CSV-zip 导出
    main.py          # FastAPI 路由（含生产模式 SPA 托管）
    storage.py       # 上传文件与方案的内存存储
  scripts/generate_samples.py
  tests/             # 40 个 pytest（解析、字符集、映射、导出、API 端到端）
frontend/
  src/
    App.vue                       # 上传/浏览/映射/预览/导出单页
    components/
      RecordList.vue FieldViewer.vue MappingEditor.vue
      IssueList.vue RawSegments.vue
samples/               # 由生成脚本产出的 7 个样例文件
```

## 运行

后端（建议虚拟环境）：

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
.venv/bin/python -I backend/scripts/generate_samples.py   # 生成样例到 samples/
cd backend && ../.venv/bin/python -I -m uvicorn app.main:app --port 8000
```

前端开发模式（热更新，:5173 代理 /api 到 :8000）：

```bash
cd frontend
npm_config_cache=.npm-cache npm install
npm run dev
```

生产模式：`npm run build` 后，FastAPI 会自动托管 `frontend/dist`，
单进程访问 <http://127.0.0.1:8000/> 即可。

测试：

```bash
cd backend && ../.venv/bin/python -I -m pytest tests/ -q
```

## 样例数据

| 文件 | 覆盖场景 |
| --- | --- |
| `sample_multilingual.mrc` | UTF-8 阿拉伯/中/俄/希腊多语种、重复 `$a`、重复 650 |
| `sample_repeated_subfields.mrc` | 同标签重复、同子字段重复、顺序敏感 |
| `sample_marc8_combining.mrc` | MARC-8 ANSEL 组合变音、G0 字符集转义切换与回退 |
| `sample_marc8_eacc.mrc` | MARC-8 EACC 四字节转义 + 三字节中日韩表意文字 |
| `sample_bad_bytes_utf8.mrc` | 非法 UTF-8 序列与孤立续接字节，原字节保留 |
| `sample_bad_bytes_marc8.mrc` | 未映射 MARC-8 字节与截断转义，原字节保留 |
| `sample_corrupt_chain.mrc` | 长度不符/缺终止符/坏目录各一条且与好记录交错，尾部不可恢复垃圾触发致命停止 |

## API 摘要

- `POST /api/datasets` 上传；`GET /api/datasets/{id}` 汇总
- `GET /api/datasets/{id}/records/{n}` 字段/指示符/子字段/字节详情
- `GET .../records/{n}/raw` 下载该记录的原始 ISO 2709 字节
- `GET|PUT .../schemes[...]` 查看/保存映射方案
- `GET .../preview/{n}` 已存方案预览；`POST .../preview/{n}` 未保存草稿实时预览（不落库）
- `POST .../export` + `GET .../download/{file}` 导出与下载
