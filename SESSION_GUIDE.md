# Information Retrieval Project — Session Guide

Οδηγίες για να σταματάμε και να συνεχίζουμε την εργασία στον ίδιο υπολογιστή.

## 1. Πριν σταματήσω για σήμερα

### Αποθήκευση και Git

Από το Terminal του PyCharm, βεβαιώσου ότι βρίσκεσαι στον κεντρικό φάκελο του project και εκτέλεσε:

Στο powershell:
git status


Έλεγξε τις αλλαγές πριν τις προσθέσεις. Τα αρχεία του dataset (`data/raw/cfc/`) και το virtual environment (`.venv/`) πρέπει να παραμένουν εκτός Git, όπως ορίζει το `.gitignore`.

Έπειτα:

Στο powershell:
git add .
git status
git commit -m "Implement Elasticsearch import, inverted index and custom BM25"
git push


Πριν από το `commit`, έλεγξε ξανά το `git status` και βεβαιώσου ότι δεν εμφανίζονται το `.venv/` ή τα αρχεία του αρχικού dataset. Το `git push` ανεβάζει τα commits στο απομακρυσμένο repository· δεν ανεβάζει αλλαγές που δεν έχουν γίνει commit.

> Αν το Git αναφέρει ότι δεν υπάρχει upstream branch για το πρώτο push, χρησιμοποίησε την εντολή που θα προτείνει. Συνήθως είναι `git push -u origin main`, εφόσον το branch σου λέγεται `main`.

### Τερματισμός Elasticsearch

Η Elasticsearch τρέχει στο πρώτο WINDOWS PowerShell και πρέπει να μείνει ανοιχτή όσο δουλεύουμε.

Όταν τελειώσεις:

1. Πήγαινε στο PowerShell όπου τρέχει η Elasticsearch.
2. Πάτησε `Ctrl+C` μία φορά.
3. Περίμενε να εμφανιστούν τα μηνύματα τερματισμού και να επιστρέψει η γραμμή εντολών.
4. Μετά μπορείς να κλείσεις το παράθυρο και το PyCharm.

Μην κλείνεις απότομα το παράθυρο όσο ο server γράφει ή επεξεργάζεται δεδομένα.

## 2. Πώς συνεχίζω την επόμενη φορά στον ίδιο υπολογιστή

### Βήμα 1 — Άνοιγμα project

Άνοιξε το project `information-retrieval-project` στο PyCharm.

### Βήμα 2 — Εκκίνηση Elasticsearch

Άνοιξε ένα WINDOWS PowerShell και πήγαινε στον φάκελο όπου έχεις αποσυμπιέσει την Elasticsearch. Αν την έχεις αποσυμπιέσει στο `C:\elasticsearch-9.5.6`, εκτέλεσε:

Στο powershell:
cd C:\elasticsearch-9.5.6
.\bin\elasticsearch.bat

Άφησε αυτό το παράθυρο ανοιχτό. Δεν χρειάζεται να εκκινήσεις δεύτερο Elasticsearch.

### Βήμα 3 — Έλεγχος ότι λειτουργεί

Άνοιξε δεύτερο WINDOWS PowerShell ή χρησιμοποίησε το Terminal του PyCharm και εκτέλεσε:

Στο powershell:
Invoke-RestMethod http://localhost:9200

Πρέπει να εμφανιστούν πληροφορίες για το node, το cluster και την έκδοση της Elasticsearch.

### Βήμα 4 — Python environment

Στο Terminal του PyCharm, βεβαιώσου ότι βρίσκεσαι στον κεντρικό φάκελο του project. Αν το virtual environment δεν έχει ενεργοποιηθεί αυτόματα, εκτέλεσε:

.\.venv\Scripts\Activate.ps1

Αν το PyCharm έχει ήδη επιλέξει το `.venv` ως Python interpreter, συνήθως δεν χρειάζεται να το ενεργοποιήσεις χειροκίνητα.


