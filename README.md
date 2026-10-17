# AI Student Notes — Complete Handwritten PDF Generator

Paste/upload a **text-based syllabus PDF**, choose **2 / 5 / 10 marks**, and press **Generate questions, handwritten answers & PDF**. Topics are split deterministically, each question is answered using local Qwen3 4B with thinking disabled, answers are rendered on ruled notebook pages, and the completed pages are combined into a PDF in question order. The PDF link appears after the first finished answer and can be downloaded again as more answers finish. For a final PDF, wait until the job status says `complete`.

## Windows setup

In Command Prompt, go to `AI_Student_Notes\backend` and run:

```bat
python -m venv venv
venv\Scripts\activate
python -m pip install -r requirements.txt
set OLLAMA_MODEL=qwen3:4b
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

If you already have a working venv, skip creating it. If Device Guard blocks `uvicorn.exe`, use `python -m uvicorn` as shown.

In another terminal, go to `AI_Student_Notes\frontend`:

```bat
npm install
npm run dev
```

Open the URL shown by Vite, normally http://localhost:5173. Ensure Ollama is running and `ollama list` includes `qwen3:4b`.

## Notes

- The model is CPU-only on your computer; 15 ten-mark answers may still take a long time. The PDF is downloadable progressively.
- The PDF is a **single document containing the rendered handwritten pages**, not typed text.
- Generated jobs persist under `backend/generated_jobs`; restarting while a job is running interrupts it. Use Resume unfinished questions.
- Scanned PDFs need OCR; paste text for now.
- The topic splitter treats hyphens and line breaks as topic separators. Review the question list for coverage.
- The renderer supports multiple pages per answer when needed; it does not promise exactly one page.
- Generated content may contain mistakes; review before studying.

## Two input options

- **Topic**: enter one subject (for example, Human Resource Planning) and click Generate 15 topic questions & PDF. Ollama plans exactly 15 distinct questions, then answers and renders them.
- **Syllabus**: paste the full syllabus or upload a text PDF. Click Generate syllabus questions, answers & PDF. One question is created per detected topic, so the count varies with the syllabus. Separate topics using commas, semicolons, new lines or dashes. Review the generated list before relying on coverage.
- The answer marks and image format controls apply to both inputs.
- Both modes share the progress preview, resume action, image ZIP and combined handwritten PDF download.

Changed files: `frontend/src/main.jsx`, `backend/main.py`, `backend/syllabus_pipeline.py`, this README; added `backend/test_input_modes.py`.

Offline validation: `python -m unittest discover -s backend -p test_input_modes.py -v` (mocked AI, real image rendering/PDF). Live AI generation requires your running Ollama model.

Topic questions now follow a beginner-friendly interview style: What is, Why use, Purpose, Role, and Difference between. Questions progress from fundamentals to specific concepts.

## If topic questions do not appear

Start Ollama and run `ollama pull qwen3:4b` once. In the backend terminal set `OLLAMA_MODEL` to the exact installed model name, then restart the backend. Restart the frontend after replacing the project. Question planning accepts numbered lists and JSON; failed jobs show the model error and can be resumed. The question list opens automatically once planning completes.
