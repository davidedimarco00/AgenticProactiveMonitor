import os
from pathlib import Path
import tempfile
import uuid

from flask import Flask, flash, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from services.document_processor import SUPPORTED_EXTENSIONS, chunk_text, extract_text
from services.embedding_service import EmbeddingService
from services.qdrant_service import QdrantService


QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "monitored-system")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")
OLLAMA_EMBEDDING_MODEL = os.getenv(
    "OLLAMA_EMBEDDING_MODEL",
    "ibm/granite-embedding:30m",
)
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "25"))
CHUNK_SIZE_WORDS = int(os.getenv("CHUNK_SIZE_WORDS", "220"))
CHUNK_OVERLAP_WORDS = int(os.getenv("CHUNK_OVERLAP_WORDS", "40"))
SEARCH_LIMIT = int(os.getenv("SEARCH_LIMIT", "5"))


app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-this-flask-secret")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

embedding_service = EmbeddingService(OLLAMA_URL, OLLAMA_EMBEDDING_MODEL)
qdrant_service = QdrantService(QDRANT_URL, QDRANT_COLLECTION)


def allowed_file(filename: str) -> bool:
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in SUPPORTED_EXTENSIONS
    )


@app.get("/")
def index():
    documents = []
    qdrant_error = None

    try:
        documents = qdrant_service.list_documents()
    except Exception as exc:
        qdrant_error = str(exc)

    return render_template(
        "index.html",
        documents=documents,
        results=[],
        search_query="",
        qdrant_error=qdrant_error,
        collection=QDRANT_COLLECTION,
        embedding_model=OLLAMA_EMBEDDING_MODEL,
        max_upload_mb=MAX_UPLOAD_MB,
        supported_extensions=sorted(SUPPORTED_EXTENSIONS),
    )


@app.post("/upload")
def upload_document():
    if "file" not in request.files:
        flash("No file was provided.", "error")
        return redirect(url_for("index"))

    uploaded_file = request.files["file"]
    if not uploaded_file.filename:
        flash("Select a document before uploading.", "error")
        return redirect(url_for("index"))

    if not allowed_file(uploaded_file.filename):
        flash("Unsupported format. Use PDF, DOCX, TXT or MD.", "error")
        return redirect(url_for("index"))

    filename = secure_filename(uploaded_file.filename)
    if not filename:
        flash("The filename is not valid.", "error")
        return redirect(url_for("index"))

    extension = filename.rsplit(".", 1)[1].lower()
    document_id = str(uuid.uuid4())
    temporary_path = None

    try:
        with tempfile.NamedTemporaryFile(
            prefix="kb_",
            suffix=f".{extension}",
            delete=False,
        ) as temporary_file:
            temporary_path = temporary_file.name
            uploaded_file.save(temporary_path)

        text = extract_text(temporary_path)
        chunks = chunk_text(
            text,
            chunk_size_words=CHUNK_SIZE_WORDS,
            overlap_words=CHUNK_OVERLAP_WORDS,
        )

        if not chunks:
            raise ValueError("The document did not produce any text chunks.")

        embeddings = embedding_service.embed_texts(chunks)
        qdrant_service.upsert_document(
            document_id=document_id,
            filename=filename,
            file_type=extension,
            chunks=chunks,
            embeddings=embeddings,
        )

        flash(
            f"{filename} uploaded successfully: {len(chunks)} chunks stored in Qdrant.",
            "success",
        )
    except Exception as exc:
        flash(f"Upload failed: {exc}", "error")
    finally:
        if temporary_path:
            try:
                Path(temporary_path).unlink(missing_ok=True)
            except OSError:
                pass

    return redirect(url_for("index"))


@app.post("/delete/<document_id>")
def delete_document(document_id: str):
    try:
        uuid.UUID(document_id)
        qdrant_service.delete_document(document_id)
        flash("Document deleted from Qdrant.", "success")
    except Exception as exc:
        flash(f"Delete failed: {exc}", "error")

    return redirect(url_for("index"))


@app.post("/search")
def search():
    query = request.form.get("query", "").strip()
    results = []
    documents = []
    qdrant_error = None

    if not query:
        flash("Enter a query to test the knowledge base.", "error")
        return redirect(url_for("index"))

    try:
        query_vector = embedding_service.embed_query(query)
        results = qdrant_service.search(query_vector, limit=SEARCH_LIMIT)
        documents = qdrant_service.list_documents()
    except Exception as exc:
        qdrant_error = str(exc)
        flash(f"Search failed: {exc}", "error")

    return render_template(
        "index.html",
        documents=documents,
        results=results,
        search_query=query,
        qdrant_error=qdrant_error,
        collection=QDRANT_COLLECTION,
        embedding_model=OLLAMA_EMBEDDING_MODEL,
        max_upload_mb=MAX_UPLOAD_MB,
        supported_extensions=sorted(SUPPORTED_EXTENSIONS),
    )


@app.get("/health")
def health():
    qdrant_ok = False
    try:
        qdrant_service.collection_info()
        qdrant_ok = True
    except Exception:
        pass

    ollama_ok = embedding_service.health()
    status = 200 if qdrant_ok and ollama_ok else 503
    return {
        "status": "ok" if status == 200 else "degraded",
        "qdrant": qdrant_ok,
        "ollama": ollama_ok,
        "collection": QDRANT_COLLECTION,
        "embedding_model": OLLAMA_EMBEDDING_MODEL,
    }, status


@app.errorhandler(413)
def too_large(_error):
    flash(f"File too large. Maximum size: {MAX_UPLOAD_MB} MB.", "error")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
