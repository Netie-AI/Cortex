# The document answer was the first 400 characters of any file sharing one word (2026-10-18)

Found while answering the founder's question on why a Space ask after a failed model step gets an unhelpful answer. Fixed by RAG-01.

## Expected vs actual
- **Expected:** a question about the documents gets an answer drawn from the passage that answers it, citing that passage. A question the documents do not answer gets no document answer.
- **Actual:** `query_service.rag_answer` served the first 400 characters of the first file containing any one question word of four letters or more. Every bench question below expects an abstain. On main, `POST /v1/contract/ask` answered all five with the Acme contract header block, which carries no answer to any of them:
  - `golden:fallback_best_price`, "Which supplier gave us the best price last quarter?", matched on "supplier";
  - `adversarial:best_price_no_fallback`, matched on "supplier";
  - `adversarial:esg_weather`, "Correlate supplier ESG scores with weather anomalies", matched on "supplier";
  - `paraphrase:fallback_turnover#0`, "stock turnover per SKU last quarter", matched on "stock";
  - `paraphrase:fallback_turnover#1`.
- **Also seen:** on main, those document answers listed every warehouse table in `contributing_sources` (alerts, inventory, locations, shipments, suppliers, transactions). A text answer claimed tables it never read. RAG-01 does not change that mapping. It is the follow-up below.

## Repro
1. On main 9936595, run `CORTEX_GOLDEN_DUMP=/tmp/d.jsonl pytest tests/dms/test_ask_flag_off_golden_340.py`.
2. Read the five qids above in `/tmp/d.jsonl`: `layer=rag` and the answer starts with "Acme Corp — Supplier Agreement".

## Root-cause class
A retrieval answer with no relevance gate is a keyword rule. One shared word decided what was served, and nothing checked that the text answered the question. The same class covers any "hit → serve" path: a search or match step must be followed by an answerability judgement (a reader model or an explicit coverage gate) before anything is served.

## Invariant
- "A wrong answer is never served" (CLAUDE.md §8/§9).
- "Assert the artifact the customer receives": the envelope dump showed it; no SQL or route assertion could.

## Fix (RAG-01)
- BM25 ranks passages, and a passage needs at least two of the question's terms.
- The model answers only from the numbered passages through FreeRoute and must cite them, or reply NOT_IN_DOCUMENTS.
- With no model available, the best sentences are quoted with their citation, and the answer says why no model summarised them. A quote also needs the passage to cover at least half of the question's terms.
- After the fix, all five bench questions abstain. The other 158 golden answers are byte-identical to main.
- Later change (wave1/a2-rag-cited-or-nothing): the quote fallback is removed. It was served under badge=document when the model failed or cited nothing, and only word overlap chose the quote, which is the same root-cause class. A failed or unarmed model, an unaccepted reply, NOT_IN_DOCUMENTS, or a reply without a valid citation now serves nothing. The answer engine abstains; the `/dms/query` legacy bridge returns its existing miss shape (empty answer, `route=rag`, no badge). All 163 golden 340 answers are unchanged, because none of them was a document answer.

## Follow-up (not done)
- The contract route's `contributing_sources` for a `layer=rag` answer should name the documents, not the warehouse grant.
- Documents are read from one global folder, not per Space (`CONTRACTS_DIR`). A real Space document store has to scope the corpus to the caller's grant before more documents are added.
