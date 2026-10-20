"""Paragraph-level chunks and citation mapping preserve prose provenance."""
import hashlib

import pytest

from packages.common.config import Settings
from packages.contracts.assessment import IndexRequest
from packages.document_processing.bibliography import catalog_references, map_citations
from packages.document_processing.chunks import chunk_pages
from packages.providers.bibliography import page as source_page, plain_html
from packages.providers.vector import VectorStore


def page(text, paragraphs=None, number=1):
    result = {
        "physical_page_number": number, "normalized_text": text, "raw_text": text,
        "status": "EXTRACTED", "blocks": [], "quality": {},
    }
    if paragraphs is not None:
        spans = []
        cursor = 0
        for paragraph in paragraphs:
            start = text.index(paragraph, cursor)
            end = start + len(paragraph)
            spans.append({"offset_start": start, "offset_end": end})
            cursor = end
        result["quality"] = {"prose_filter_version": "paragraphs-v1", "prose_paragraphs": spans}
    return result


def chunks(pages, **changes):
    options = {"origin": "MANUSCRIPT", "document_id": "document", "document_version_id": "v1", "user_id": "user"}
    return chunk_pages(pages, **(options | changes))["chunks"]


def catalog():
    return {
        "status": "COMPLETED", "bibliography_regions": [],
        "references": [
            {"id": "silva", "authors": ["Silva, Ana"], "year": 2020, "year_suffix": None},
            {"id": "costa", "authors": ["Costa, Bia"], "year": 2021, "year_suffix": None},
        ],
    }


def test_one_chunk_is_one_complete_paragraph_without_overlap_or_window_splitting():
    first = "Primeiro parágrafo com contexto suficiente. " * 200
    second = "Outro parágrafo descreve uma conclusão diferente."
    text = first + "\n\n" + second
    source = page(text, [first, second])
    result = chunks([source], size=100, overlap=90)

    assert [chunk["text"] for chunk in result] == [first, second]
    assert len(result[0]["text"]) > 1800
    assert result[0]["offset_end"] <= result[1]["offset_start"]
    assert [chunk["ordinal"] for chunk in result] == [0, 1]
    for chunk in result:
        assert chunk["text"] == text[chunk["offset_start"]:chunk["offset_end"]]
        assert chunk["content_hash"] == hashlib.sha256(chunk["text"].encode()).hexdigest()
    assert result == chunks([source])


def test_chunk_excludes_table_image_and_diagram_text_selected_out_by_extractor():
    first = "A análise descreve o método empregado no estudo."
    last = "Os participantes apresentaram resultados compatíveis com a hipótese."
    text = (
        first + "\n\nTabela 1: Silva (2020)\nGrupo | N | Valor\nA | 40 | 12%\n\n"
        "Figura 1. Costa (2021) descreve a imagem.\n\n"
        "Início -> avaliação -> resultado (Silva, 2020).\n\n" + last
    )
    selected = page(text, [first, last])
    for origin in ("MANUSCRIPT", "SOURCE"):
        result = chunks([selected], origin=origin, source_id="source", work_id="work")
        assert [chunk["text"] for chunk in result] == [first, last]
        assert all(chunk["origin"] == origin for chunk in result)


def test_explicit_empty_prose_selection_never_restores_tabular_or_visual_text():
    source = page("Tabela 1: Silva (2020)\nA | B | C\n40 | 30 | 20", [])
    assert chunks([source]) == []
    mapped = map_citations([source], catalog())
    assert mapped["occurrences"] == []
    assert mapped["claims"] == []


def test_legacy_pages_split_at_paragraph_boundaries_without_splitting_wrapped_lines():
    first = "Um parágrafo mantém sua frase na primeira linha\ne continua até a conclusão do argumento."
    second = "O próximo parágrafo trata de um tema diferente."
    assert [chunk["text"] for chunk in chunks([page(first + "\n\n" + second)])] == [first, second]


def test_downloaded_abstract_with_text_only_block_keeps_separate_prose_paragraphs():
    first = "O resumo apresenta os objetivos e métodos empregados."
    second = "Os resultados descrevem as limitações observadas no estudo."
    abstract = plain_html(f"<p>{first}</p><table><tr><td>Resultados numéricos</td></tr></table><p>{second}</p>")
    downloaded = source_page(abstract, method="PUBLISHER_ABSTRACT")
    assert downloaded["blocks"][0]["bbox"] is None
    assert [chunk["text"] for chunk in chunks([downloaded], origin="SOURCE", source_id="source", work_id="work")] == [first, second]


def test_source_chunk_identity_stays_stable_and_preserves_source_and_page_scope():
    text = "A fonte apresenta uma passagem completa para recuperar evidências."
    pages = [page(text, [text], number=7)]
    options = {"origin": "SOURCE", "source_id": "source-1", "work_id": "work", "version": "2"}
    first = chunks(pages, **options)[0]
    assert first == chunks(pages, **options)[0]
    assert first["page_start"] == first["page_end"] == 7
    assert first["source_id"] == "source-1"
    assert first["id"] != chunks(pages, **(options | {"source_id": "source-2"}))[0]["id"]
    assert first["id"] != chunks(pages, **(options | {"version": "3"}))[0]["id"]


def test_paragraph_above_embedding_limit_is_preserved_and_reported_without_silent_cut(monkeypatch):
    paragraph = "Um parágrafo excepcionalmente longo mantém sua proveniência e seu conteúdo. " * 400
    selected = chunks([page(paragraph, [paragraph])])
    assert len(selected) == 1 and selected[0]["text"] == paragraph
    store = VectorStore(Settings(_env_file=None, embedding_provider="ollama"))
    monkeypatch.setattr(store.embedding.http, "request", lambda *args, **kwargs: pytest.fail("Oversized text must fail before HTTP."))
    result = store.index(IndexRequest(user_id="user", document_id="document", chunks=selected))
    assert result["indexed_ids"] == []
    assert result["failed_ids"] == [selected[0]["id"]]
    assert any("limite" in warning for warning in result["limitations"])


def test_multiple_citations_share_complete_paragraph_claim_but_keep_individual_references():
    first_sentence = "Silva (2020) descreveu melhora na atenção."
    second_sentence = "Costa (2021) relatou limitações na amostra."
    paragraph = first_sentence + " " + second_sentence
    mapped = map_citations([page(paragraph, [paragraph])], catalog(), scope="run")

    assert len(mapped["claims"]) == 1
    claim = mapped["claims"][0]
    assert claim["text"] == paragraph
    assert claim["metadata"]["segmentation"] == "paragraphs-v1"
    assert claim["reference_ids"] == ["costa", "silva"]
    assert [occ["reference_ids"] for occ in mapped["occurrences"]] == [["silva"], ["costa"]]
    assert [occ["sentence"] for occ in mapped["occurrences"]] == [first_sentence, second_sentence]
    assert all(occ["claim_ids"] == [claim["id"]] for occ in mapped["occurrences"])
    assert all(occ["paragraph"] == paragraph for occ in mapped["occurrences"])


def test_citation_mapping_excludes_nonprose_without_losing_offsets_or_paragraphs():
    first = "Silva (2020) apresentou os resultados. Uma segunda frase contextualiza os dados."
    second = "A discussão de Costa (2021) explica as limitações do método."
    text = "Tabela 1: Costa (2021)\n10 | 20 | 30\n\n" + first + "\n\nFigura 1: Silva (2020)\n\n" + second
    mapped = map_citations([page(text, [first, second])], catalog())

    assert [claim["text"] for claim in mapped["claims"]] == [first, second]
    assert len(mapped["occurrences"]) == 2
    for occurrence in mapped["occurrences"]:
        assert text[occurrence["offset_start"]:occurrence["offset_end"]] == occurrence["marker"]
        assert "Tabela" not in occurrence["paragraph"]
        assert "Figura" not in occurrence["paragraph"]


def test_bibliography_remains_available_even_when_it_is_not_selected_as_prose():
    paragraph = "Silva (2020) apresentou resultados relevantes."
    references = "Referências\nSILVA, Ana. Estudo sobre atenção. 2020."
    pages = [page(paragraph, [paragraph]), page(references, [], number=2)]
    references_catalog = catalog_references(pages)
    assert len(references_catalog["references"]) == 1
    mapped = map_citations(pages, references_catalog)
    assert len(mapped["claims"]) == 1
    assert mapped["claims"][0]["text"] == paragraph
    assert mapped["occurrences"][0]["binding_status"] == "RESOLVED"
    assert mapped["pages"][1]["status"] == "BIBLIOGRAPHIC"


def test_citation_marker_cannot_be_invented_across_distinct_paragraphs():
    first = "O texto termina com o nome Silva"
    second = "(2020) inicia uma observação independente."
    mapped = map_citations([page(first + "\n\n" + second, [first, second])], catalog())
    assert mapped["occurrences"] == []
    assert mapped["claims"] == []


def test_bibliography_start_inside_a_prose_span_does_not_enter_claim_context():
    paragraph = "Silva (2020) apresentou um resultado importante."
    text = paragraph + "\nReferências\nSILVA, Ana. Estudo. 2020."
    source = page(text, [text])
    mapped = map_citations([source], catalog_references([source]))
    assert len(mapped["claims"]) == 1
    assert mapped["claims"][0]["text"] == paragraph
