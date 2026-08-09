import os
import chromadb
from chromadb.utils import embedding_functions

class ClinicalVectorStore:
    def __init__(self, db_path="data/chroma_db", collection_name="loinc_concepts"):
        """
        Initializes the Vector Database.
        db_path: Where the vectors will be physically stored on disk.
        collection_name: The name of our "dictionary" (we can use 'loinc' now and 'snomed' in the future).
        """
        # Ensures the directory exists (we'll store it alongside the DuckDB database)
        os.makedirs(db_path, exist_ok=True)
        
        # The ChromaDB PersistentClient saves data to disk so we don't have 
        # to recalculate the math every time we run the script
        self.client = chromadb.PersistentClient(path=db_path)
        
        # The "Translator" (Uses sentence-transformers under the hood)
        # The all-MiniLM-L6-v2 model is incredibly fast and perfect for short local texts
        self.embedding_fn = embedding_functions.DefaultEmbeddingFunction()
        
        # Creates or connects to the collection (the vector "table")
        # We use 'cosine' because cosine distance is the standard for comparing text semantics
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=self.embedding_fn,
            metadata={"hnsw:space": "cosine"} 
        )

    def populate_vocabulary(self, concepts):
        """
        Receives a list of dictionaries with the official concepts and stores them as vectors.
        Ex: [{'concept_id': 3006422, 'concept_name': 'Glucose [Mass] in Blood', 'concept_code': '2339-0'}]
        """
        # If we already have data, there's no need to run the embedding again!
        if self.collection.count() > 0:
            print(f"✅ Vector store '{self.collection.name}' is already populated with {self.collection.count()} concepts.")
            return

        print(f"🧠 Translating {len(concepts)} concepts into vector space... This might take 1-2 minutes.")
        
        ids = []
        documents = []
        metadatas = []
        
        for concept in concepts:
            ids.append(str(concept['concept_id']))  # Chroma requires IDs to be strings
            documents.append(concept['concept_name']) # The text that will become math
            metadatas.append({                        # Extra information to use later
                "concept_code": concept['concept_code'],
                "concept_name": concept['concept_name']
            })
        
        # We insert in batches of 5000 to avoid overloading the computer's RAM
        batch_size = 5000
        for i in range(0, len(ids), batch_size):
            self.collection.add(
                ids=ids[i:i+batch_size],
                documents=documents[i:i+batch_size],
                metadatas=metadatas[i:i+batch_size]
            )
            print(f"   ⏳ Processed {min(i+batch_size, len(ids))} / {len(ids)} concepts...")
            
        print("🚀 Vector database successfully built!")

    def search(self, query_text, top_k=5):
        """
        The magic happens here: receives a dirty text and returns the 'top_k' closest semantic concepts.
        """
        results = self.collection.query(
            query_texts=[query_text],
            n_results=top_k
        )
        return results