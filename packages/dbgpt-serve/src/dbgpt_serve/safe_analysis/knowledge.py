"""Reuse DB-GPT document readers for operator-approved metric documents.

Database rows and files are different: rows enter DuckDB, documents become
retrievable text. A PDF is never treated as an SQL table automatically.
"""

from pathlib import Path

from .contracts import AnalysisError


def load_knowledge(paths):
    for file_number, filename in enumerate(paths):
        path = Path(filename)
        suffix = path.suffix.lower()
        if suffix == ".md":
            from dbgpt_ext.rag.knowledge.markdown import MarkdownKnowledge

            reader = MarkdownKnowledge(file_path=str(path))
        elif suffix == ".txt":
            from dbgpt_ext.rag.knowledge.txt import TXTKnowledge

            reader = TXTKnowledge(file_path=str(path))
        elif suffix == ".docx":
            from dbgpt_ext.rag.knowledge.docx import DocxKnowledge

            reader = DocxKnowledge(file_path=str(path))
        elif suffix == ".pdf":
            from dbgpt_ext.rag.knowledge.pdf import PDFKnowledge

            reader = PDFKnowledge(file_path=str(path))
        else:
            raise AnalysisError("口径文档格式不受支持")
        documents = reader.load()
        count = 0
        for document in documents:
            # Overlapping fixed windows keep this educational implementation
            # explicit. DB-GPT's richer splitters can replace this function.
            for start in range(0, len(document.content), 1300):
                count += 1
                if count > 500:
                    raise AnalysisError("单个口径文档超过 500 个切片")
                yield (
                    f"document:{file_number}:{count}",
                    f"来源: {path.name}\n" + document.content[start : start + 1500],
                )
