# PP-22A 公开 OUTCAR 夹具

来源为 materialsproject/pymatgen 固定提交 `975fed926f3b2606648ddb57679ddceaeac10d8a`，下载日期 2026-10-11。保留上游 [MIT 许可证](LICENSE.txt)。三份文件均为该项目公开的 VASP 输出测试文件，完整下载后原字节保留；没有使用用户科研文件，也没有下载或分发 POTCAR 正文。OUTCAR 内的 TITEL 等输出元数据并不证明真实 POTCAR 哈希一致。

固定来源目录：[test-files/io/vasp/outputs](https://github.com/materialsproject/pymatgen/tree/975fed926f3b2606648ddb57679ddceaeac10d8a/test-files/io/vasp/outputs)。许可证来源：[上游 LICENSE](https://github.com/materialsproject/pymatgen/blob/975fed926f3b2606648ddb57679ddceaeac10d8a/LICENSE)。

| 文件 | SHA256（保存字节） | 验证范围 |
|---|---|---|
| `OUTCAR.Al` | `02068c13eab5d7ca8a3cdadca046884eaf453a1e8a09f0c97f6abf52605663a3` | VASP 5.2.2，PBE 共线静态，Al 1；三个字段均为 -3.743359 eV，最终离子汇总第 6084–6088 行 |
| `OUTCAR.serial.gz` | `a1b4c7f63768b25682ddba374bb307616c6f8af50128dcca610eb7553b0d8ab2` | 原始文件没有版本 banner；PBE 共线弛豫，Si 8；一离子步，三个字段均为 -43.39175481 eV；最终汇总第 1599–1603 行 |
| `OUTCAR.etest1.gz` | `80d10c3fd8b76fde517f17cb5497945cf28544999e36f6373e6bad0dd9083131` | VASP 5.4.1，GGA=RE，Cu 1，八个完整离子汇总块；末块第 4264–4268 行，E0=-11.18981538、without entropy=-11.13480014、TOTEN=-11.217323 eV |

真实文件验证了离子汇总版式、组成与证据定位，以及最后完整块的三字段数值。`OUTCAR.etest1.gz` 的 GGA=RE 用于验证多步和不同字段，解析保留方法并提示未在首版 PBE 自动比较范围内；不将它冒充 PBE 多步验证。PBE+U 的逐元素元数据映射目前由合成测试覆盖，没有宣称真实 PBE+U 科学验收。

`backend/tests/test_energy_parser.py` 中的短文本全部是明确构造的合成片段，只验证跨步混配、尾部截断、多段／编号重置、非有限数值、缺组成、重复元素声明、有限数值格式、运行类型及收敛证据归属等程序行为。它们不是 VASP 计算结果。

独立字段核查使用 pymatgen `Outcar.final_energy`、`final_energy_wo_entrp`、`final_fr_energy`；不把该库最后属性用作本模块完整块／收敛归属判据。真实格式与库数值对照不证明参考态选择、参数收敛、吸附／形成能科学结果或跨版本全面支持；未运行 VASP、真实 HPC、外部模型或服务。

字段与状态语义参考：[VASP O atom](https://vasp.at/wiki/O_atom)、[ALGO](https://vasp.at/wiki/ALGO)、[IALGO](https://vasp.at/wiki/IALGO)、[LDAUTYPE](https://vasp.at/wiki/LDAUTYPE)。电子迭代标题 `Free energy of ...` 与最终离子标题 `FREE ENERGIE OF ...` 分开；`ALGO=Exact` 属于自洽算法，固定轨道 `None/Eigenval` 及响应／GW 不归为普通 SCF 比较。
