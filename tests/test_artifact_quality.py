import unittest

import artifact_quality


class ArtifactQualityTest(unittest.TestCase):
    def test_rules_reject_undeclared_targets_typo_and_wrong_types(self):
        for rules in ({'other.md': {'min_chars': 1}}, {'report.md': {'min_char': 1}},
                      {'report.md': {'min_chars': True}}, {'report.md': {'min_rows': 1}},
                      {'report.md': {'require_title_and_locator': 'yes'}}):
            with self.subTest(rules=rules), self.assertRaises(ValueError):
                artifact_quality.validate_rules({'required_files': ['report.md'], 'file_rules': rules})
        artifact_quality.validate_rules({'required_files': ['report.md', 'refs.bib'], 'file_rules': {
            'report.md': {'min_chars': 2500}, 'refs.bib': {'min_entries': 8, 'require_title_and_locator': True}}})

    def test_report_below_required_length_names_file_and_actual_count(self):
        with self.assertRaisesRegex(ValueError, r"review/report\.md: min_chars.*2500.*1200"):
            artifact_quality.validate("review/report.md", "研" * 1200, {"min_chars": 2500})

    def test_character_minimum_ignores_unicode_whitespace_and_accepts_boundary(self):
        with self.assertRaisesRegex(ValueError, r"non-whitespace characters; found 2"):
            artifact_quality.validate("report.md", "研 \n\t究\u3000", {"min_chars": 3})
        self.assertIsNone(artifact_quality.validate("report.md", "研 \n\t究\u3000", {"min_chars": 2}))

    def test_no_rules_does_not_replace_existing_syntax_validation(self):
        self.assertIsNone(artifact_quality.validate("matrix.csv", '"unfinished', {}))
        self.assertIsNone(artifact_quality.validate("references.bib", "@article{", {}))

    def test_csv_minimum_excludes_header_and_empty_records(self):
        content = "title,doi\nStudy,10.test/one\n\n , \n"
        with self.assertRaisesRegex(ValueError, r"matrix\.csv: CSV min_rows.*2.*found 1"):
            artifact_quality.validate("matrix.csv", content, {"min_rows": 2})
        self.assertIsNone(artifact_quality.validate("matrix.csv", content, {"min_rows": 1}))

    def test_csv_required_columns_and_multiline_records(self):
        content = '\ufeff title , doi ,note\r\n"Study, one",10.test/1,"two\nlines"\r\n'
        self.assertIsNone(artifact_quality.validate("matrix.CSV", content, {
            "min_rows": 1, "required_columns": ["title", "doi"],
        }))
        with self.assertRaisesRegex(ValueError, r"matrix\.csv: CSV missing required_columns: year, url"):
            artifact_quality.validate("matrix.csv", content, {"required_columns": ["doi", "year", "url"]})

    def test_csv_header_is_required_and_columns_are_case_sensitive(self):
        with self.assertRaisesRegex(ValueError, "CSV requires a header row"):
            artifact_quality.validate("empty.csv", "\n , \n", {"min_rows": 1})
        with self.assertRaisesRegex(ValueError, "missing required_columns: title"):
            artifact_quality.validate("matrix.csv", "Title,doi\nA,B", {"required_columns": ["title"]})

    def test_methods_metrics_rejects_unmapped_rows_without_a_locator(self):
        header = "citation,interface,modification_method,material_system,conditions,metric,value,unit,evidence_level,comparability_note,doi_or_stable_url\n"
        valid = 'Study,Li/electrolyte,coating,LLZO,not reported,not reported,,,abstract-level,Not in abstract; verify full text,https://doi.org/10.test/study\n'
        invalid = 'result_id=abc,unknown,unknown,unknown,,,,,metadata-only,No source mapping,\n'

        self.assertIsNone(artifact_quality.validate("methods_metrics.csv", header + valid, {}))
        with self.assertRaisesRegex(ValueError, "citation and DOI or stable URL"):
            artifact_quality.validate("methods_metrics.csv", header + valid + invalid, {})

    def test_methods_metrics_must_cover_accepted_candidate_titles_and_locators(self):
        candidates = (
            "title,doi_or_stable_url\n"
            'Study One,https://doi.org/10.1234/one\n'
            'Study Two,http://arxiv.org/abs/2401.00001v1\n'
        )
        header = "citation,doi_or_stable_url\n"
        valid = (
            'Study One,10.1234/one\n'
            'Study Two,https://arxiv.org/abs/2401.00001v1\n'
        )
        artifact_quality.validate_methods_metrics_candidates("methods_metrics.csv", header + valid, candidates)

        with self.assertRaisesRegex(ValueError, "row 2 does not match an accepted candidate"):
            artifact_quality.validate_methods_metrics_candidates(
                "methods_metrics.csv", header + "result_id=abc,\n" + valid.splitlines()[1] + "\n", candidates)
        with self.assertRaisesRegex(ValueError, "row 2 does not match an accepted candidate"):
            artifact_quality.validate_methods_metrics_candidates(
                "methods_metrics.csv", header + "Study One,10.1234/wrong\n" + valid.splitlines()[1] + "\n", candidates)

    def test_methods_metrics_requires_distinct_candidate_coverage_and_allows_multiple_metrics(self):
        candidates = "title,doi_or_stable_url\nStudy One,https://doi.org/10.1234/one\nStudy Two,https://example.org/two\n"
        header = "citation,doi_or_stable_url\n"
        row = "Study One,10.1234/one\n"
        with self.assertRaisesRegex(ValueError, "missing accepted A candidates: Study Two"):
            artifact_quality.validate_methods_metrics_candidates("methods_metrics.csv", header + row + row, candidates)
        with self.assertRaisesRegex(ValueError, "missing accepted A candidates: Study Two"):
            artifact_quality.validate_methods_metrics_candidates("methods_metrics.csv", header + row, candidates)
        artifact_quality.validate_methods_metrics_candidates(
            "methods_metrics.csv", header + row + row + "Study Two,https://example.org/two\n", candidates)

    def test_bibtex_counts_sources_and_ignores_directives_and_embedded_at_signs(self):
        content = '''
% @misc{in_line_comment, title={Fake}, doi={fake}}
@comment{Ignore @article{inside_comment, title={Fake}, doi={fake}}}
@preamble{"Contact person@example.org"}
@string{journal = "Journal @misc{inside_string}"}
@article{first, title={Real @misc{inside_title}}, doi={10.test/one}}
@book(second, TITLE="A second source", URL="https://example.org/books/two")
'''
        rule = {"min_entries": 2, "require_title_and_locator": True}
        self.assertIsNone(artifact_quality.validate("references.bib", content, rule))
        with self.assertRaisesRegex(ValueError, r"references\.bib: BibTeX min_entries.*3.*found 2"):
            artifact_quality.validate("references.bib", content, {**rule, "min_entries": 3})

    def test_six_placeholder_sources_fail_even_when_entry_minimum_is_met(self):
        content = "\n".join(f"@misc{{placeholder{i}, note={{Pending verification}}}}" for i in range(6))
        with self.assertRaisesRegex(ValueError, r"entry 'placeholder0'.*nonempty title.*doi.*url"):
            artifact_quality.validate("references.bib", content, {
                "min_entries": 6, "require_title_and_locator": True,
            })

    def test_parenthesized_directives_do_not_count_as_sources(self):
        content = '''
@comment(Ignore (nested text) and @misc{fake, title={Not a source}})
@preamble("Text with a closing ) delimiter")
@string(prefix = "https://example.org/(source)")
'''
        with self.assertRaisesRegex(ValueError, r"min_entries.*1.*found 0"):
            artifact_quality.validate("references.bib", content, {"min_entries": 1})

    def test_nested_braced_and_quoted_titles_with_escapes(self):
        content = r'''
@article{braced, title={The {deep {nested}} model, with "quotes" and \{symbols\}},
    doi={10.test/nested}}
@article(quoted, title="The {nested {title with a "protected quote"}} and \"escaped quotes\"",
    url={https://example.org/paper?key=quoted})
'''
        self.assertIsNone(artifact_quality.validate("references.bib", content, {
            "min_entries": 2, "require_title_and_locator": True,
        }))

    def test_deeply_nested_title_does_not_depend_on_python_recursion(self):
        title = "{" * 1500 + "Nested title" + "}" * 1500
        content = "@article{deep, title={" + title + "}, doi={10.test/deep}}"
        self.assertIsNone(artifact_quality.validate("references.bib", content, {
            "min_entries": 1, "require_title_and_locator": True,
        }))

    def test_duplicate_source_keys_do_not_inflate_the_count(self):
        content = "@article{same, title={One}, doi={10.test/1}}\n@misc{same, title={Two}, doi={10.test/2}}"
        with self.assertRaisesRegex(ValueError, r"references\.bib: BibTeX duplicate citation key 'same'"):
            artifact_quality.validate("references.bib", content, {"min_entries": 2})

    def test_title_and_locator_are_checked_for_every_source(self):
        valid = "@article{valid, title={Complete}, doi={10.test/one}}\n"
        invalid = (
            ("title={{ }}, doi={10.test/two}", "nonempty title"),
            ('title=" ", doi={10.test/two}', "nonempty title"),
            ("title={Has a title}, doi={{ }}", "nonempty doi or absolute HTTP"),
            ("title={Has a title}, url={paper.pdf}", "nonempty doi or absolute HTTP"),
            ("title={Has a title}, url={https://}", "nonempty doi or absolute HTTP"),
            ("title={Has a title}, url={https://example.org/bad path}", "nonempty doi or absolute HTTP"),
        )
        for fields, message in invalid:
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, f"entry 'bad'.*{message}"):
                artifact_quality.validate("references.bib", valid + "@misc{bad, " + fields + "}", {
                    "min_entries": 1, "require_title_and_locator": True,
                })

    def test_valid_url_or_doi_and_concatenated_literals_satisfy_locator_rule(self):
        for locator in ('doi="10.test/one"', "url={https://example.org/one}",
                        'url="http://example.org/" # "two"'):
            with self.subTest(locator=locator):
                self.assertIsNone(artifact_quality.validate("references.bib", "@misc{one, title={Title}, " + locator + "}", {
                    "min_entries": 1, "require_title_and_locator": True,
                }))

    def test_malformed_bibtex_has_file_specific_value_errors(self):
        for content in ('@article{broken, title={Unclosed}', '@article{broken, title="Unclosed}',
                        '@article{broken, title={Title} doi={10.test/one}}',
                        '@article{broken, title={First}, title={Second}}'):
            with self.subTest(content=content), self.assertRaisesRegex(ValueError, r"references\.bib: BibTeX.*character"):
                artifact_quality.validate("references.bib", content, {"min_entries": 1})


if __name__ == "__main__":
    unittest.main()
