"""OpenJM-owned DOCX extractor that preserves tables and paragraph order."""

from typing import Any, Dict, List, Optional, Union

import docx
from docx.oxml.text.paragraph import CT_P
from docx.oxml.table import CT_Tbl
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.opc.pkgreader import _SerializedRelationships

from dbgpt.core import Document
from dbgpt_ext.rag.knowledge.docx import DocxKnowledge


class OpenJMDocxKnowledge(DocxKnowledge):
    """Docx knowledge that extracts tables in addition to paragraphs, preserving order."""

    def _load(self) -> List[Document]:
        """Load docx document from file, extracting paragraphs and tables in order."""
        docs = []
        if self._loader:
            documents = self._loader.load()
            # Convert loaded documents to Document objects if needed
            for doc in documents:
                if isinstance(doc, Document):
                    docs.append(doc)
                else:
                    # Assume it's a Langchain document
                    docs.append(Document(content=doc.page_content, metadata=doc.metadata))
        else:
            # We need to allow the monkey-patch from factory.py to work
            _SerializedRelationships.load_from_xml = self._load_from_xml_v2  # type: ignore
            doc = docx.Document(self._path)
            content_parts = []

            # Iterate over the document body to preserve order
            for element in doc.element.body:
                if isinstance(element, CT_P):
                    para = Paragraph(element, doc)
                    text = para.text
                    if text:
                        content_parts.append(text)
                elif isinstance(element, CT_Tbl):
                    table = Table(element, doc)
                    markdown_table = self._table_to_markdown(table)
                    if markdown_table:
                        content_parts.append(markdown_table)
                # Ignore other element types (like comments) for simplicity

            content = "\n\n".join(content_parts)
            metadata = {"source": self._path}
            if self._metadata:
                metadata.update(self._metadata)  # type: ignore
            docs.append(Document(content=content, metadata=metadata))

        return docs

    @staticmethod
    def _load_from_xml_v2(base_uri, rels_item_xml):
        """Return |_SerializedRelationships| instance loaded with the relationships.

        contained in *rels_item_xml*.collection if *rels_item_xml* is |None|.
        Copied from parent to avoid modifying upstream.
        """
        from docx.opc.oxml import parse_xml
        from docx.opc.pkgreader import _SerializedRelationship, _SerializedRelationships

        srels = _SerializedRelationships()
        if rels_item_xml is not None:
            rels_elm = parse_xml(rels_item_xml)
            for rel_elm in rels_elm.Relationship_lst:
                if rel_elm.target_ref in ("../NULL", "NULL"):
                    continue
                srels._srels.append(_SerializedRelationship(base_uri, rel_elm))
        return srels

    def _table_to_markdown(self, table: Table) -> str:
        """Convert a docx table to a markdown string.

        Simple implementation: assumes no merged cells, all cells have text.
        """
        if not table.rows:
            return ""

        # Determine number of columns from the first row
        num_cols = len(table.rows[0].cells)
        if num_cols == 0:
            return ""

        # Build rows of cell text
        rows: List[List[str]] = []
        for row in table.rows:
            cells = []
            for cell in row.cells:
                # Get text, strip, and replace newlines with spaces for markdown compatibility
                text = cell.text.strip().replace("\n", " ")
                cells.append(text)
            # Pad or truncate to num_cols
            if len(cells) < num_cols:
                cells.extend([""] * (num_cols - len(cells)))
            elif len(cells) > num_cols:
                cells = cells[:num_cols]
            rows.append(cells)

        # Build markdown
        lines = []
        # Header separator
        separator = ["---"] * num_cols
        for i, row in enumerate(rows):
            lines.append("| " + " | ".join(row) + " |")
            if i == 0:
                lines.append("| " + " | ".join(separator) + " |")

        return "\n".join(lines)