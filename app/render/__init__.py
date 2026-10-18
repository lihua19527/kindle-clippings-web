# -*- coding: utf-8 -*-
"""导出器：Markdown / Word / PDF。

三种格式共用同一份「书 → 条目」数据，保证内容一致：
  · Markdown —— 直接调用原脚本的 render_book / render_index，和你以前导出的文件逐字一致
  · Word     —— python-docx 生成真正的 .docx（笔记标题用 Word 原生黄色高亮）
  · PDF      —— 先生成打印用 HTML，再用本机 Edge 的 headless 模式 --print-to-pdf
                （文字可选中、可搜索，中文字体随文件嵌入，不依赖任何第三方 PDF 库）
"""
