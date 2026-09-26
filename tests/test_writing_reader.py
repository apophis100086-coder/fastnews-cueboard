"""Source-reading edge cases; synthetic bytes only, no live requests."""
import unittest
from unittest.mock import Mock, patch

from fastnews.writing_fetch import PublicationMetadata, read_page
from fastnews.writing_checks import changed_quantity_scopes


class WritingReaderTests(unittest.TestCase):
    def test_conflicting_publication_dates_do_not_use_last_tag(self):
        parser=PublicationMetadata()
        parser.feed('<meta property="article:published_time" content="2026-09-15">'
                    '<meta name="publishdate" content="2026-09-18">')
        self.assertIsNone(parser.published_at)

    def test_compatible_date_precision_and_modified_date(self):
        parser=PublicationMetadata()
        parser.feed('<meta name="publishdate" content="2026-09-15">'
                    '<meta property="article:published_time" content="2026-09-15T08:00:00+08:00">'
                    '<meta property="article:modified_time" content="2026-09-18">')
        self.assertEqual(parser.published_at,'2026-09-15T08:00:00+08:00')

    def test_scanned_pdf_page_labels_cannot_count_as_extracted_text(self):
        response=Mock(headers={'content-type':'application/pdf'},body=b'synthetic',url='https://example.org/report.pdf')
        pages=[Mock(extract_text=Mock(return_value=None if i%2 else '')) for i in range(20)]
        with patch('fastnews.writing_fetch.SafeHTTPClient') as client,patch('pypdf.PdfReader') as reader:
            client.return_value.get.return_value=response
            reader.return_value.is_encrypted=False;reader.return_value.pages=pages
            with self.assertRaisesRegex(ValueError,'pdf_requires_ocr'):
                read_page(response.url)

    def test_explicit_bounds_and_approximation_keep_meaning(self):
        for text,evidence in [('超过90%','90%以上'),('20亿元','约20亿元'),('低于20%','20%以下'),('20%','不到20%')]:
            with self.subTest(text=text):self.assertTrue(changed_quantity_scopes(text,evidence))
        for text,evidence in [('不低于90%','90%以上'),('至多20%','20%以下'),('约20亿元','20亿元左右'),('20%','20%')]:
            with self.subTest(text=text):self.assertFalse(changed_quantity_scopes(text,evidence))
