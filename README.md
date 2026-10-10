# 婴儿脑分割

Infant Brain Segmentation · T2脑提取、脑区分割、质量复核与体积计算。

本项目整理一条可检查、可追溯的研究流程：**独立脑提取 → 掩膜复核 → 全新DrawEM → 脑区复核 → 个体空间体积计算**。标题使用“婴儿脑分割”，但当前方法主要面向新生儿，年龄模板门槛为扫描时胎龄（PMA）28–44周；不能直接推断适用于全部婴儿月龄或所有异常解剖。

公开仓库仅含方法文档、自编核查代码和合成测试；不含患者影像、人工勾画、病例编号、临床结果、历史敏感日志或第三方模型/图谱。

## 流程图

![婴儿脑分割研究流程](workflow.svg)

这里的“先提取脑”是生成脑掩膜，不是只裁出一个头部方框；处理保留全视野T2，并在新工作目录中让通过复核的脑掩膜参与后续预处理。任何质量门槛未通过都进入复核/修复，不能因程序成功退出而自动纳入研究。

## 两条分割路线

| 路线 | 脑提取 | 后续处理 | 要检查的重点 |
| --- | --- | --- | --- |
| A：基线 | DrawEM预处理中的BET | N4、图谱先验、多图谱配准、EM和后处理 | 头皮/眼眶/面颈漏入，脑缘、小脑或脑干漏掉 |
| B：重点候选 | ANUBEX-SA独立脑掩膜，先人工复核 | 在**空目录**中重新完成整条DrawEM链路 | 掩膜范围、CSF兼容性，以及内部脑区边界 |

B是待验证候选，不是已证明全面优于A。脑提取好不等于海马、丘脑等内部脑区一定正确。与模板单次配准后直接传播标签相比，DrawEM还结合组织强度、先验及EM分类，但同样可能受掩膜、图谱域差异和异常解剖影响。方法选择必须靠区域级复核。

## 先跑不含患者数据的示例

Python 3.10或更新版本，进入仓库后：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
neonatal-audit demo --output runs/synthetic-demo
```

打开 `runs/synthetic-demo/comparison/report.html`。示例只有合成几何体，不是真实婴儿MRI；演示全图＋局部显示、体积、Dice、边界距离与输入校验。已有输出目录拒绝覆盖。

## 具体实现步骤

完整命令、目录布局、环境限制和验收清单见 [IMPLEMENTATION.md](IMPLEMENTATION.md)。

1. **准备输入**：经授权的全视野3D T2、扫描时PMA、有效毫米空间头信息。保留采集原图；如统一到1mm处理网格，要保存重采样记录，不把它叫作采集原始分辨率。
2. **核对空间**：检查左右方向、affine、qform/sform、体素大小及覆盖范围。A/B用同一输入网格；标签传播使用最近邻或离散标签算法，灰度图才用连续插值。
3. **独立脑提取**：在兼容nnUNet v1环境获取并核验ANUBEX-SA模型，生成二值掩膜。`scripts/predict_brain_mask.py`提供固定五折、TTA和禁用连通域后处理的适配入口。
4. **掩膜门槛**：三向连续看脑缘、眼眶、后枕、CSF、小脑/脑干。未通过先修复和记录，不通过自动删最大连通域“美化”结果。
5. **全新DrawEM**：在空目录预置 `<subject>_brain_mask.nii.gz`，核实上游跳过BET条件；重新产生N4、组织先验、配准、EM与后处理，不复用旧中间文件。
6. **脑区门槛**：在相同患者背景、同物理位置、同窗宽窗位上看全图＋局部＋连续邻层；重点核查灰白质、基底节、丘脑、海马、小脑/脑干和脑室。
7. **体积与方法比较**：在最终个体网格计量，输出CSV/JSON；参考勾画协议先统一。重合度/质心差是技术一致性，不直接等于准确率/解剖偏移。
8. **可追溯归档**：记录输入、掩膜、标签、配置、软件/模型版本及SHA-256；技术失败和病变排除分开。区域通过复核后才进入研究统计。

## 本仓库能直接做什么

```bash
neonatal-audit compare --config examples/compare.json --output runs/comparison
neonatal-audit volumes --labels /path/to/case_all_labels.nii.gz --output runs/volumes
neonatal-audit --help
```

`compare`支持自定义正整数标签组，要求同网格，不自动配准、重采样或清理标签。`volumes`采用明确冻结的DHF87研究分组，不适用于任意图谱或纯人工海马标签。27个输出含双侧/总量及重叠分组，不能把全部行相加当作总脑体积。定义与限制见 [METRICS.md](METRICS.md)。

`plan-drawem`仅生成准备计划；分割需另备MIRTK/DrawEM、FSL、图谱、ANUBEX权重和兼容nnUNet环境。本仓库不承诺一条安装命令完成全脑分割，也不会自动执行Docker或批量扩展。

## 文档与安全边界

- [IMPLEMENTATION.md](IMPLEMENTATION.md)：具体实现步骤、示例命令与运行边界。
- [QC_CHECKLIST.md](QC_CHECKLIST.md)：掩膜/区域/人工标注的复核清单。
- [METRICS.md](METRICS.md)：体积与一致性定义。
- [PRIVACY.md](PRIVACY.md)：公开范围及数据安全。
- [THIRD_PARTY.md](THIRD_PARTY.md)：上游工具、来源与许可边界。
- `examples/`：比较配置与流程参数；`tests/`：无患者资料的单元测试。

本项目不是诊断产品；没有提供临床准确性、全队列通过率或适用于所有脑区的保证。公开可见不等于自动授予开放源代码许可；本仓库暂未新增许可证，第三方资源按各自许可获取与引用。更新时间：2026-10-10。
