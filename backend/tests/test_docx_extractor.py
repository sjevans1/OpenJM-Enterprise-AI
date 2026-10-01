"""Unit tests for the OpenJM DOCX extractor."""

import tempfile
import os
from docx import Document as DocxDocument

from app.services.docx_extractor import OpenJMDocxKnowledge


def test_docx_extractor_preserves_paragraphs_and_tables():
    """Test that the extractor preserves paragraphs and tables in order."""
    # Create a temporary DOCX file with known structure
    doc = DocxDocument()
    doc.add_paragraph('Header')
    doc.add_paragraph('Some text before table')
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = 'A'
    table.cell(0, 1).text = 'B'
    table.cell(1, 0).text = '1,425'
    table.cell(1, 1).text = 'C'
    doc.add_paragraph('Footer after table')
    
    tmp_path = tempfile.mktemp(suffix='.docx')
    doc.save(tmp_path)
    
    try:
        extractor = OpenJMDocxKnowledge(file_path=tmp_path)
        docs = extractor._load()
        
        # We expect one Document object
        assert len(docs) == 1
        content = docs[0].content
        
        # Check that paragraphs are present
        assert 'Header' in content
        assert 'Some text before table' in content
        assert 'Footer after table' in content
        
        # Check that the table is converted to markdown and present
        # The markdown table should look like:
        # | A | B |
        # | --- | --- |
        # | 1,425 | C |
        assert '| A | B |' in content
        assert '| --- | --- |' in content
        assert '| 1,425 | C |' in content
        
        # Check that the specific value is present
        assert '1,425' in content
        
        # Check that the order is approximately correct (header separator comes after header row)
        # We'll do a simple check: the string '| A | B |' appears before '| --- | --- |'
        assert content.index('| A | B |') < content.index('| --- | --- |')
        assert content.index('| --- | --- |') < content.index('| 1,425 | C |')
        
    finally:
        os.unlink(tmp_path)


def test_docx_extractor_handles_empty_table():
    """Test that an empty table does not break extraction."""
    doc = DocxDocument()
    doc.add_paragraph('Before')
    table = doc.add_table(rows=0, cols=0)  # This might not be valid, let's do 1x1
    # Actually, let's do a 1x1 empty table
    doc.add_table(rows=1, cols=1)
    doc.add_paragraph('After')
    
    tmp_path = tempfile.mktemp(suffix='.docx')
    doc.save(tmp_path)
    
    try:
        extractor = OpenJMDocxKnowledge(file_path=tmp_path)
        docs = extractor._load()
        assert len(docs) == 1
        content = docs[0].content
        assert 'Before' in content
        assert 'After' in content
        # The table might produce an empty markdown table or nothing; we just want no exception
    finally:
        os.unlink(tmp_path)


def test_docx_extractor_metadata_preserved():
    """Test that source metadata is set."""
    doc = DocxDocument()
    doc.add_paragraph('Test')
    
    tmp_path = tempfile.mktemp(suffix='.docx')
    doc.save(tmp_path)
    
    try:
        extractor = OpenJMDocxKnowledge(file_path=tmp_path)
        docs = extractor._load()
        assert len(docs) == 1
        assert docs[0].metadata.get('source') == tmp_path
    finally:
        os.unlink(tmp_path)