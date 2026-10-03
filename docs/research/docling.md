# Docling：V1 选型记录

Primary Parser，Phase 2 已接入；正式运行时依赖 docling>=2,<3，实测 Docling 2.132.0。
使用公开 DocumentConverter、DoclingDocument export/iterate APIs；所有具体类型与标签映射限定于 Adapter。
PDF/EPUB/DOCX/Markdown 实际转换测试使用原创 fixtures；TXT 使用自有读取器。
PDF 默认不启用 OCR，启用表格结构；首次转换可能下载官方模型资源。

官方参考：
- https://docling-project.github.io/docling/reference/document_converter/
- https://docling-project.github.io/docling/reference/docling_document/

此处不声明商业书籍、扫描件、复杂公式和多栏版式的完整识别精度。
