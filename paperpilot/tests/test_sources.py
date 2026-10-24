from __future__ import annotations

import unittest

from app.services import search


class SourceParserTests(unittest.TestCase):
    def test_europepmc_parser_normalizes_abstract_and_open_access(self) -> None:
        payload = {
            "resultList": {
                "result": [
                    {
                        "id": "40000001",
                        "source": "MED",
                        "pmid": "40000001",
                        "doi": "10.1000/example",
                        "title": "Efficient Inference for Language Models",
                        "authorString": "Ada Lovelace, Alan Turing",
                        "journalTitle": "Journal of Efficient Computing",
                        "pubYear": "2025",
                        "abstractText": "We propose an efficient inference method.",
                        "isOpenAccess": "Y",
                        "fullTextUrlList": {
                            "fullTextUrl": [
                                {
                                    "availability": "Open access",
                                    "documentStyle": "pdf",
                                    "url": "https://example.org/paper.pdf",
                                }
                            ]
                        },
                    }
                ]
            }
        }
        papers = search._parse_europepmc(payload)
        self.assertEqual(len(papers), 1)
        paper = papers[0]
        self.assertEqual(paper["source"], "europepmc")
        self.assertEqual(paper["doi"], "10.1000/example")
        self.assertEqual(paper["authors"], ["Ada Lovelace", "Alan Turing"])
        self.assertEqual(paper["year"], 2025)
        self.assertEqual(paper["pdf_url"], "https://example.org/paper.pdf")
        self.assertEqual(paper["sections"][0]["text"], "We propose an efficient inference method.")

    def test_doaj_parser_reads_bibjson_and_doi(self) -> None:
        payload = {
            "results": [
                {
                    "bibjson": {
                        "title": "Open Access Paper on Quantization",
                        "abstract": "A study of low-bit quantization.",
                        "author": [{"name": "Grace Hopper"}, {"name": "Barbara Liskov"}],
                        "year": "2024",
                        "journal": {"title": "Open Systems Journal"},
                        "identifier": [
                            {"type": "eissn", "id": "1234-5678"},
                            {"type": "doi", "id": "10.2000/doaj-example"},
                        ],
                        "link": [{"type": "fulltext", "url": "https://example.org/doaj-paper"}],
                    }
                }
            ]
        }
        papers = search._parse_doaj(payload)
        self.assertEqual(len(papers), 1)
        paper = papers[0]
        self.assertEqual(paper["source"], "doaj")
        self.assertEqual(paper["doi"], "10.2000/doaj-example")
        self.assertEqual(paper["title"], "Open Access Paper on Quantization")
        self.assertEqual(paper["authors"], ["Grace Hopper", "Barbara Liskov"])
        self.assertEqual(paper["year"], 2024)
        self.assertEqual(paper["venue"], "Open Systems Journal")

    def test_hal_parser_reads_fields_and_pdf(self) -> None:
        payload = {
            "response": {
                "docs": [
                    {
                        "docid": "hal-12345",
                        "title_s": ["Hardware-Aware Quantization for Edge AI"],
                        "abstract_s": ["We study hardware-aware quantization."],
                        "authFullName_s": ["Ada Lovelace", "Grace Hopper"],
                        "producedDateY_i": 2023,
                        "doiId_s": "10.3000/hal-example",
                        "uri_s": "https://hal.example/hal-12345",
                        "journalTitle_s": "HAL Open Science",
                        "fileMain_s": "https://hal.example/hal-12345/document",
                    }
                ]
            }
        }
        papers = search._parse_hal(payload)
        self.assertEqual(len(papers), 1)
        paper = papers[0]
        self.assertEqual(paper["source"], "hal")
        self.assertEqual(paper["doi"], "10.3000/hal-example")
        self.assertEqual(paper["year"], 2023)
        self.assertEqual(paper["pdf_url"], "https://hal.example/hal-12345/document")
        self.assertTrue(paper["sections"])

    def test_public_source_parsers_skip_incomplete_records(self) -> None:
        self.assertEqual(search._parse_europepmc({"resultList": {"result": [{"id": "1"}]}}), [])
        self.assertEqual(search._parse_doaj({"results": [{"bibjson": {"abstract": "x"}}]}), [])
        self.assertEqual(search._parse_hal({"response": {"docs": [{"docid": "x"}]}}), [])


if __name__ == "__main__":
    unittest.main()
