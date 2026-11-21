---
name: scheduled-literature-matrix
description: 为文献矩阵定时任务整理同一主题下可对比的论文证据。
---

用 `paper_discover` 按主题和任务要求的天数筛有确切发表日期的论文，按 DOI 去重。必要时用 `paper_abstracts` 或 `paper_focus` 查已收录论文。在只有摘要的情况下，数据集、方法细节、结果和局限以摘要明确写出的信息为限。

最终回复使用「题目 / 方法 / 数据 / 结论 / 局限」Markdown 表格，题目链接到 DOI 或来源。注明查询范围和证据来自摘要还是正文，未知字段留空。如果没有适合的论文，说明检索条件和未找到的范围。
