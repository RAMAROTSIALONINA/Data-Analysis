document.getElementById('gemini-form').addEventListener('submit', async function(e) {
    e.preventDefault();

    const form = e.target;
    // Mampiasa FormData ho an'ny fandefasana données miaraka amin'ny fichier (multipart/form-data)
    const formData = new FormData(form);
    
    const submitBtn = document.getElementById('submit-btn');
    const loadingIndicator = document.getElementById('loading-indicator');
    const responseOutput = document.getElementById('response-output');

    // Manomboka ny dingana: manakana sy mampiseho ny chargement
    submitBtn.disabled = true;
    loadingIndicator.classList.remove('hidden');
    responseOutput.textContent = 'Mandefa ny fangatahana any amin\'ny FastAPI...';
    responseOutput.style.color = 'black';

    try {
        // Antsoina ny endpoint ao amin'ny FastAPI
        const response = await fetch('/api/process_query', {
            method: 'POST',
            body: formData, // Tsy mila manisy 'Content-Type' eto, FormData no manao an'izany
        });

        const result = await response.json();

        // Mampiseho ny valiny na ny erreur
        if (response.ok) {
            responseOutput.textContent = result.response;
        } else {
            // Mandray ny erreur avy amin'ny FastAPI
            responseOutput.textContent = `ERREUR (${response.status}): ${result.detail || 'Erreur inconnue du serveur.'}`;
            responseOutput.style.color = 'red';
        }

    } catch (error) {
        console.error('Erreur de la requête:', error);
        responseOutput.textContent = 'Erreur de connexion: Tsy tafiditra tamin\'ny serveur backend FastAPI.';
        responseOutput.style.color = 'red';
    } finally {
        // Mamita ny dingana: manala ny fanakanana sy manafina ny chargement
        submitBtn.disabled = false;
        loadingIndicator.classList.add('hidden');
    }
});