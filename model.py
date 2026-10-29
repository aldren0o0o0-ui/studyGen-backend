import chromadb.utils.embedding_functions as ef
# This will trigger the download and wait until it finishes
print("Downloading embedding model... please wait.")
model = ef.ONNXMiniLM_L6_V2()
print("Model downloaded successfully!")