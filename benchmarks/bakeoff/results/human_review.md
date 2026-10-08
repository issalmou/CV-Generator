# Human quality review — writing / chat outputs

Read for: fidelity to the source CV, hallucinations, ATS relevance, writing quality, prompt-constraint compliance.



==========================================================================================
# ats_content_optimization
==========================================================================================


--- groq / openai/gpt-oss-120b (mode=none, 4.2s, 1874 tok) ---

{
    "optimized_summary": "Ingénieur backend senior avec 4 ans d’expérience, spécialisé en Python, FastAPI et bases de données PostgreSQL, déployées sur AWS via Terraform. Expert en conception d’APIs REST haute performance, orchestration de conteneurs avec Kubernetes et mentorat d’équipes de développeurs.",
    "career_objective": "Contribuer à la conception et à l’évolution de services de données distribués à fort trafic à Casablanca en mobilisant mon expertise Python, Kubernetes et mentorat.",
    "optimized_achievements": [
        {
            "original_role": "Ingenieur Backend Senior @ Atlas Data",
            "bullets": [
                "Conçu et livré un service de facturation traitant 2 M de transactions mensuelles, en utilisant FastAPI et PostgreSQL sur AWS.",
                "Réduit la latence p95 de l’API de 800 ms à 210 ms grâce à un cache Redis, optimisation des requêtes et orchestration Kubernetes.",
                "Mentoré deux développeurs juniors, instauré des revues de code systématiques et automatisé les pipelines CI/CD avec Docker."
            ]
        },
        {
            "original_role": "Developpeur Backend @ Beta Solutions",
            "bullets": [
                "Développé les microservices d’authentification et de paiement d’une marketplace, en implémentant des APIs REST sécurisées avec Python.",
                "Migré une base monolithique MySQL vers PostgreSQL sans interruption de service, assurant la continuité des transactions."
            ]
        },
        {
            "original_role": "Stagiaire Developpeur @ Startup Nova",
            "bullets": [
                "Implémenté un tableau de bord analytique interne, visualisant les métriques clés pour les équipes produit."
            ]
        }
    ],
    "additional_skills": ["Kubernetes", "Mentorat", "CI/CD", "Observabilité", "Docker"]
}


--- groq / qwen/qwen3.8-27b (mode=none, 1.2s, 385 tok) ---

{
  "optimized_summary": "Ingénieur backend senior avec 4 ans d'expérience en Python, FastAPI et PostgreSQL pour des systèmes distribués à fort trafic. Expert en AWS, Terraform et Kubernetes, avec une forte capacité de mentorat des équipes juniors.",
  "career_objective": "Rejoindre une équipe technique pour concevoir des APIs REST performantes et orchestrer des services de données distribués sur Kubernetes.",
  "optimized_achievements": [
    {
      "original_role": "Ingenieur Backend Senior @ Atlas Data",
      "bullets": [
        "Conçu un service de facturation traitant 2M de transactions mensuelles via Python et FastAPI.",
        "Réduit la latence p95 de 800ms à 210ms grâce à Redis et l'optimisation PostgreSQL.",
        "Assure le mentorat de deux développeurs juniors et instaure une revue de code systématique."
      ]
    },
    {
      "original_role": "Developpeur Backend @ Beta Solutions",
      "bullets": [
        "Développé des microservices d'authentification et de paiement pour une marketplace à fort trafic.",
        "Migré une base monolithique MySQL vers PostgreSQL sans interruption de service."
      ]
    },
    {
      "original_role": "Stagiaire Developpeur @ Startup Nova",
      "bullets": [
        "Implémenté un tableau de bord analytique interne pour le suivi des données."
      ]
    }
  ],
  "additional_skills": [
    "Kafka",
    "gRPC",
    "Docker",
    "CI/CD",
    "Observabilité"
  ]
}


--- gemini / gemini-3.5-flash-lite (mode=none, 1.7s, 370 tok) ---

{
    "optimized_summary": "Ingénieur logiciel backend avec 4 ans d'expérience sur des plateformes de données et des APIs à fort trafic. Spécialisé en Python, systèmes distribués et cloud, avec une expertise démontrée en mentorat technique et en orchestration Kubernetes.",
    "career_objective": "Intégrer le poste de Senior Backend Engineer pour concevoir et faire évoluer des services de données distribués à fort trafic en utilisant Python et AWS.",
    "optimized_achievements": [
        {
            "original_role": "Ingenieur Backend Senior @ Atlas Data",
            "bullets": [
                "Conçu et livré un service de facturation traitant 2M de transactions/mois, tout en assurant son déploiement via Kubernetes.",
                "Réduit la latence p95 de l'API de 800ms à 210ms via un cache Redis et l'optimisation des requêtes PostgreSQL.",
                "Encadré 2 développeurs juniors grâce à des activités de mentorat structurées et la mise en place de revues de code."
            ]
        },
        {
            "original_role": "Developpeur Backend @ Beta Solutions",
            "bullets": [
                "Développé les microservices d'authentification et de paiement d'une marketplace en utilisant Python et FastAPI.",
                "Migré avec succès une base monolithique MySQL vers PostgreSQL sans aucune interruption de service pour les utilisateurs."
            ]
        },
        {
            "original_role": "Stagiaire Developpeur @ Startup Nova",
            "bullets": [
                "Implémenté un tableau de bord analytique interne performant pour faciliter le suivi des indicateurs clés."
            ]
        }
    ],
    "additional_skills": [
        "Kubernetes",
        "Terraform",
        "Kafka",
        "gRPC"
    ]
}


--- gemini / gemini-3.1-flash-lite (mode=none, 1.9s, 388 tok) ---

{
  "optimized_summary": "Ingénieur Backend Senior avec 4 ans d'expérience dans la conception de services distribués à fort trafic. Expert en Python, FastAPI et PostgreSQL, je maîtrise l'orchestration avec Kubernetes et le déploiement via AWS et Terraform. Passionné par l'optimisation des performances et le mentorat d'équipes techniques.",
  "career_objective": "Contribuer en tant que Senior Backend Engineer à l'évolution des services distribués de votre plateforme grâce à mon expertise en Python, AWS et Kubernetes.",
  "optimized_achievements": [
    {
      "original_role": "Ingenieur Backend Senior @ Atlas Data",
      "bullets": [
        "Conçu un service de facturation traitant 2M de transactions/mois en utilisant Python et FastAPI pour des APIs REST performantes.",
        "Réduit la latence p95 de 800ms à 210ms via l'optimisation des requêtes PostgreSQL et l'implémentation stratégique d'un cache Redis.",
        "Assuré le mentorat de 2 développeurs juniors et instauré des pratiques de revue de code systématique pour garantir la qualité logicielle."
      ]
    },
    {
      "original_role": "Developpeur Backend @ Beta Solutions",
      "bullets": [
        "Développé des microservices d'authentification et de paiement, orchestrés via Kubernetes pour assurer la scalabilité des services sur AWS.",
        "Migré une base monolithique MySQL vers PostgreSQL sans interruption de service, tout en automatisant l'infrastructure avec Terraform."
      ]
    },
    {
      "original_role": "Stagiaire Developpeur @ Startup Nova",
      "bullets": [
        "Implémenté un tableau de bord analytique interne en utilisant Python pour visualiser les données critiques de l'entreprise."
      ]
    }
  ],
  "additional_skills": [
    "Kafka",
    "gRPC"
  ]
}


--- gemini / gemini-3.6-flash (mode=none, 18.1s, 371 tok) ---

```json
{
    "optimized_summary": "Ingénieur logiciel backend avec 4 ans d'expérience dans la conception d'APIs à fort trafic et de systèmes distribués. Spécialisé en Python et PostgreSQL, j'interviens sur des architectures Cloud complexes utilisant Kubernetes. Reconnu pour mon expérience en mentorat, je conçois des solutions distribuées scalables et hautement performantes.",
    "career_objective": "Mettre à profit mon expertise en Python, architectures distribuées et Cloud en tant que Senior Backend Engineer à Casablanca.",
    "optimized_achievements": [
        {
            "original_role": "Ingenieur Backend Senior @ Atlas Data",
            "bullets": [
                "Conçu et livré un service de facturation haute disponibilité traitant plus de 2 millions de transactions par mois.",
                "Réduit la latence p95 de l'API de 800ms à 210ms grâce à Redis et à l'optimisation avancée des requêtes.",
                "Assuré le mentorat de 2 développeurs juniors et instauré la revue de code systématique pour garantir la qualité logicielle."
            ]
        },
        {
            "original_role": "Developpeur Backend @ Beta Solutions",
            "bullets": [
                "Développé et déployé les microservices critiques d'authentification et de paiement pour une plateforme marketplace dynamique.",
                "Migré avec succès une base monolithique MySQL vers PostgreSQL sans aucune interruption de service pour les utilisateurs."
            ]
        },
        {
            "original_role": "Stagiaire Developpeur @ Startup Nova",
            "bullets": [
                "Implémenté un tableau de bord analytique interne performant pour rationaliser le suivi des indicateurs clés de performance."
            ]
        }
    ],
    "additional_skills": [
        "Kubernetes",
        "FastAPI",
        "AWS",
        "Terraform",
        "CI/CD"
    ]
}
```


==========================================================================================
# cover_letter
==========================================================================================


--- groq / openai/gpt-oss-120b (mode=none, 1.8s, 607 tok) ---

À l’attention de l’équipe de recrutement d’Atlas Recrute,

Ingénieur logiciel backend depuis quatre ans, je postule au poste de Senior Backend Engineer. Fort d’une expérience chez Atlas Data où j’ai conçu un service de facturation capable de traiter deux millions de transactions par mois, j’ai également réduit la latence p95 de notre API de 800 ms à 210 ms grâce à un cache Redis et à l’optimisation des requêtes. Encadrer deux développeurs juniors et instaurer une revue de code systématique m’a permis de renforcer la qualité du code et la cohésion de l’équipe.

Chez Beta Solutions, j’ai développé les microservices d’authentification et de paiement d’une marketplace et migré une base monolithique MySQL vers PostgreSQL sans interruption de service, démontrant ma capacité à gérer des changements critiques en production. Ces projets m’ont familiarisé avec les architectures distribuées, le tuning de bases de données et les exigences de haute disponibilité.

Le stack que vous décrivez (Python, FastAPI, PostgreSQL, Redis, Docker, AWS, Terraform) correspond exactement à mon quotidien, et je suis particulièrement motivé par l’opportunité de travailler sur l’orchestration Kubernetes, les pipelines CI/CD et l’observabilité que vous développez. Contribuer à l’évolution de services de données à fort trafic tout en partageant mon expérience de mentorat représente pour moi un défi stimulant et en phase avec mes aspirations professionnelles.

Je serais ravi d’échanger davantage sur la manière dont mon parcours peut soutenir les ambitions d’Atlas Recrute. Je vous remercie pour votre considération et vous adresse mes salutations distinguées.

Mehdi Bennani


--- groq / openai/gpt-oss-20b (mode=none, 1.3s, 730 tok) ---

Atlas Recrute,

Je m’appelle Mehdi Bennani, ingénieur logiciel backend depuis quatre ans, et je souhaite rejoindre votre équipe en tant que Senior Backend Engineer. Actuellement en poste chez Atlas Data, je conçois et maintiens des services à fort trafic, et je suis convaincu que mon expérience s’aligne parfaitement avec vos besoins.

Au cours de mon dernier mandat, j’ai conçu et déployé un service de facturation traitant plus de 2 millions de transactions par mois, tout en réduisant la latence p95 de l’API de 800 ms à 210 ms grâce à un cache Redis et à l’optimisation des requêtes. J’ai également encadré deux développeurs juniors et instauré une revue de code systématique. Avant cela, chez Beta Solutions, j’ai développé les micro‑services d’authentification et de paiement d’une marketplace, et j’ai migré une base monolithique MySQL vers PostgreSQL sans interruption de service.

Ce qui me motive particulièrement à rejoindre Atlas Recrute, c’est votre ambition de faire évoluer des services de données distribuées à fort trafic. Je suis passionné par la conception d’APIs REST performantes et l’optimisation de PostgreSQL, et je maîtrise Python, FastAPI, Redis, Docker, AWS et Terraform – les technologies clés de votre stack. Mon certificat AWS Certified Solutions Architect – Associate et mon expérience en déploiement cloud me permettront de contribuer immédiatement à vos pipelines CI/CD et à votre observabilité.

Je serais ravi de discuter plus en détail de la façon dont je pourrais soutenir vos projets. Merci de considérer ma candidature.

Mehdi Bennani


--- groq / qwen/qwen3.8-27b (mode=none, 1.3s, 347 tok) ---

Objet : Candidature au poste d'Ingénieur Backend Senior

Madame, Monsieur,

Ingénieur logiciel avec quatre années d'expérience dans la conception de plateformes de données à fort trafic, je souhaite mettre mon expertise en Python et en systèmes distribués au service de votre équipe chez Atlas Recrute. Votre recherche d'un profil capable de faire évoluer des services distribués complexes résonne particulièrement avec mon parcours et mes réalisations récentes.

Actuellement Ingénieur Backend Senior chez Atlas Data, j'ai conçu et livré un service de facturation traitant 2 millions de transactions mensuelles. J'ai également réduit la latence p95 de l'API de 800 ms à 210 ms grâce à l'optimisation des requêtes PostgreSQL et à la mise en place d'un cache Redis. Cette expérience m'a permis de développer une solide maîtrise de la performance et de la scalabilité, tout en encadrant deux développeurs juniors et en instaurant une culture rigoureuse de revue de code.

La stack technique proposée, combinant FastAPI, Kubernetes et AWS, correspond parfaitement à mes compétences actuelles et à ma certification AWS Solutions Architect. Je suis particulièrement motivé par la perspective de contribuer à l'optimisation de vos pipelines CI/CD et à l'amélioration de l'observabilité de vos services, des domaines où j'ai acquis une expertise significative lors de mes précédentes missions.

Je serais ravi d'échanger avec vous sur la manière dont mon profil peut s'inscrire dans la dynamique de votre équipe. Je reste à votre disposition pour un entretien à votre convenance.

Cordialement,

Mehdi Bennani


--- gemini / gemini-3.5-flash-lite (mode=none, 1.9s, 317 tok) ---

Chère équipe d'Atlas Recrute,

Je vous écris pour exprimer mon vif intérêt pour le poste de Senior Backend Engineer. Fort de quatre années d'expérience en ingénierie logicielle et diplômé de l'INSEA, je conçois et fais évoluer des plateformes de données et des API à fort trafic en mettant l'accent sur la performance et la fiabilité des systèmes distribués.

Chez Atlas Data, j'ai notamment conçu et livré un service de facturation traitant deux millions de transactions par mois. J'ai également réduit la latence p95 de notre API de 800ms à 210ms grâce à l'intégration de Redis et à l'optimisation de nos requêtes, tout en encadrant deux développeurs juniors. Auparavant, chez Beta Solutions, j'ai piloté la migration d'une base monolithique MySQL vers PostgreSQL sans aucune interruption de service et développé des microservices critiques pour une marketplace.

Rejoindre Atlas Recrute représente une suite logique pour moi, car votre besoin de concevoir des services de données distribués à fort trafic résonne directement avec mon parcours en Python, FastAPI, PostgreSQL, AWS et en architecture cloud. J'ai également à cœur de partager mon expérience à travers le mentorat, une dimension importante de mon parcours quotidien.

Je serais ravi d'échanger avec vous prochainement sur la manière dont mon expertise technique peut contribuer au succès de vos équipes.

Bien cordialement,

Mehdi Bennani


--- gemini / gemini-3.1-flash-lite (mode=none, 1.8s, 365 tok) ---

À l'attention de l'équipe de recrutement d'Atlas Recrute,

Je vous contacte avec enthousiasme pour le poste de Senior Backend Engineer. Fort de quatre années d'expérience dans la conception de systèmes distribués et d'APIs à fort trafic, je suis convaincu que mon expertise technique et ma capacité à résoudre des problématiques de performance complexes correspondent parfaitement aux besoins de votre infrastructure.

Chez Atlas Data, j'ai récemment conçu un service de facturation traitant deux millions de transactions par mois. Pour garantir une expérience utilisateur fluide, j'ai optimisé nos requêtes et intégré Redis, ce qui a permis de réduire la latence p95 de notre API de 800ms à 210ms. Par ailleurs, mon expérience chez Beta Solutions m'a permis de gérer des migrations critiques de bases de données sans interruption de service, une rigueur que je souhaite mettre au profit de vos services de données.

Votre stack technique, centrée sur Python, FastAPI et AWS, est celle sur laquelle j'évolue quotidiennement. Étant certifié AWS Solutions Architect et habitué aux environnements conteneurisés, je suis particulièrement motivé par le défi de faire évoluer vos services distribués tout en maintenant une haute disponibilité. Le mentorat technique, que j'ai pratiqué en encadrant des développeurs juniors, est également un aspect du métier qui me tient à cœur.

Je serais ravi d'échanger avec vous sur la manière dont mon approche de l'ingénierie logicielle peut contribuer à la croissance d'Atlas Recrute. Je vous remercie pour l'attention portée à ma candidature et reste à votre entière disposition pour un entretien.

Bien cordialement,

Mehdi Bennani


--- gemini / gemini-3.6-flash (mode=none, 12.4s, 299 tok) ---

À l'attention de l'équipe de recrutement d'Atlas Recrute,

Fort de quatre années d'expérience dans la conception d'architectures backend et de plateformes de données à fort trafic, je vous adresse ma candidature pour le poste de Senior Backend Engineer à Casablanca.

Au cours de mon parcours chez Atlas Data, j'ai notamment conçu un service de facturation traitant deux millions de transactions par mois et réduit la latence p95 de nos API de 800 ms à 210 ms grâce à l'optimisation de nos requêtes PostgreSQL et à l'usage de Redis sous FastAPI. Certifié AWS Solutions Architect Associate, j'ai également piloté chez Beta Solutions la migration sans interruption de service d'une base MySQL vers PostgreSQL. Habituer à encadrer deux développeurs juniors et à promouvoir la revue de code systématique, j'allie exigence technique et sens du collectif.

La perspective de faire évoluer vos services de données distribués et de concevoir des API REST performantes résonne directement avec ma pratique quotidienne de la stack Python, du cloud AWS et des environnements conteneurisés. Contribuer aux enjeux d'optimisation et d'observabilité d'Atlas Recrute constitue pour moi une suite logique et stimulante.

Je serais ravi d'échanger avec vous lors d'un entretien pour vous présenter plus en détail l'impact de mes réalisations.


==========================================================================================
# conversation_agent
==========================================================================================


--- groq / openai/gpt-oss-120b (mode=none, 0.9s, 350 tok) ---

**Titre du poste recherché**  
Senior Backend Engineer – Casablanca  

**Résumé professionnel (ATS‑friendly)**  

Ingénieur logiciel backend avec **4 ans d’expérience** dans la conception, le développement et le déploiement d’applications critiques en **Python**, **FastAPI**, **PostgreSQL** et **AWS**. Expertise avérée dans :  

- Conception d’API RESTful performantes et sécurisées (FastAPI)  
- Optimisation de bases de données relationnelles (PostgreSQL) – indexation, requêtes complexes, tuning ;  
- Architecture cloud (AWS) – EC2, RDS, S3, Lambda, CloudFormation, CI/CD avec GitHub Actions/CodePipeline ;  
- Mise en place de bonnes pratiques DevOps (Docker, Kubernetes) et de tests automatisés (pytest, unit/integration) ;  
- Collaboration agile avec équipes cross‑fonctionnelles (Scrum, Kanban) et mentorat de développeurs junior.  

**Objectif**  
Apporter mon expertise technique et mon sens du produit pour piloter des projets backend de grande envergure chez un acteur innovant de Casablanca, tout en continuant à évoluer vers des responsabilités de leadership technique.  


--- groq / openai/gpt-oss-20b (mode=none, 0.7s, 179 tok) ---

**Ingénieur logiciel backend – 4 ans d’expérience**  
Spécialisé en Python, FastAPI, PostgreSQL et déploiement sur AWS.  
Capable de concevoir, développer et maintenir des services backend robustes et évolutifs.


--- groq / qwen/qwen3.8-27b (mode=none, 1.1s, 356 tok) ---

Voici une proposition de profil réécrit, optimisé pour les ATS (systèmes de suivi des candidats) et plus impactant, en s'appuyant strictement sur les compétences et l'expérience que vous avez mentionnées :

**Profil**
Ingénieur Backend Senior avec 4 ans d'expérience dans la conception et le développement d'applications web robustes. Expertise technique approfondie en **Python** et **FastAPI** pour la création d'API haute performance, ainsi qu'en **PostgreSQL** pour la modélisation et l'optimisation des bases de données. Solide maîtrise de l'infrastructure cloud **AWS** pour le déploiement et la scalabilité des solutions. Orienté résultats, je cherche à mettre mes compétences techniques au service d'une équipe à Casablanca pour développer des architectures backend fiables et évolutives.

**Pourquoi cette version fonctionne mieux :**
1.  **Mots-clés ATS :** Les technologies clés (Python, FastAPI, PostgreSQL, AWS) sont mises en évidence et placées tôt dans le texte.
2.  **Titre clair :** "Ingénieur Backend Senior" cible directement votre objectif.
3.  **Action orientée :** Au lieu de simplement dire "avec 4 ans d'expérience", elle décrit *ce que vous faites* avec cette expérience (conception, développement, optimisation).
4.  **Localisation :** Mention de Casablanca pour montrer votre disponibilité géographique.

*Note : J'ai utilisé le terme "Senior" dans le titre comme demandé, mais assurez-vous que le reste de votre CV (projets, responsabilités) justifie ce niveau d'expérience par des réalisations concrètes.*


--- gemini / gemini-3.5-flash-lite (mode=none, 1.5s, 320 tok) ---

Voici 3 propositions de résumés percutants et optimisés ATS, basés uniquement sur ton profil (4 ans d'expérience, backend, Python/FastAPI/PostgreSQL/AWS) et ton objectif (Casablanca) :

**Option 1 (Directe et axée résultats) :**
> "Ingénieur Backend Senior spécialisé en Python, FastAPI, PostgreSQL et AWS. 4 ans d'expérience dans la conception et le développement d'architectures robustes et scalables. Actuellement basé à Casablanca, je mets mon expertise technique au service de projets à fort impact."

**Option 2 (Technique et orientée ATS) :**
> "Ingénieur Logiciel Backend avec 4 ans d'expérience. Solide maîtrise de la stack Python, FastAPI, PostgreSQL et du cloud AWS. Reconnu pour la création d'APIs performantes et la gestion de bases de données. En recherche d'un poste Senior Backend à Casablanca."

**Option 3 (Courte et percutante) :**
> "Ingénieur Backend (4 ans d'expérience) expert en Python/FastAPI, PostgreSQL et AWS. Passionné par le développement de solutions backend scalables, je souhaite apporter mon savoir-faire technique à une équipe innovante à Casablanca."

**Mon conseil ATS :** Garde les mots-clés exacts (*Ingénieur Backend, Python, FastAPI, PostgreSQL, AWS*) bien visibles dès les premières lignes de ton CV. 

Lequel de ces résumés te correspond le mieux ?


--- gemini / gemini-3.1-flash-lite (mode=none, 2.3s, 432 tok) ---

Voici trois options pour ton résumé (aussi appelé "Accroche" ou "Profil"), optimisées pour les ATS et ton objectif de poste Senior :

**Option 1 : Orientée résultats (Recommandée)**
> "Ingénieur Backend avec 4 ans d'expérience spécialisé en Python et FastAPI. Expert dans la conception d'architectures scalables sous AWS et la gestion de bases de données PostgreSQL. Orienté performance et qualité de code, je cherche à apporter mon expertise technique à une équipe à Casablanca."

**Option 2 : Orientée stack technique (Directe)**
> "Ingénieur Backend (4 ans d'expérience) maîtrisant l'écosystème Python (FastAPI). Solide expertise en modélisation PostgreSQL et déploiement d'infrastructures cloud AWS. Habitué aux environnements agiles, je souhaite évoluer vers un rôle de Senior Backend pour concevoir des solutions robustes et performantes."

**Option 3 : Orientée "Seniorité" (Pour souligner ton ambition)**
> "Ingénieur Backend confirmé avec 4 ans d'expérience en développement d'APIs haute performance (Python/FastAPI). Compétences éprouvées en optimisation de bases de données PostgreSQL et gestion de services AWS. Prêt à relever des défis techniques complexes au sein d'une équipe Senior à Casablanca."

**Conseils pour l'optimisation ATS :**
*   **Mots-clés :** J'ai intégré les termes exacts que les recruteurs recherchent (Python, FastAPI, PostgreSQL, AWS, Backend).
*   **Structure :** Ces résumés placent tes compétences techniques dès les premières lignes, ce qui est crucial pour les logiciels de tri automatique.
*   **Localisation :** Mentionner "Casablanca" aide les algorithmes de recherche locale à mieux classer ton profil.

**Quelle option préfères-tu ?** Je peux l'ajuster si tu souhaites mettre l'accent sur un aspect particulier (ex: le cloud, le clean code ou le mentoring).


--- gemini / gemini-3.6-flash (mode=none, 8.1s, 261 tok) ---

Voici deux propositions percutantes et optimisées pour les ATS, intégrant tes technologies clés sans rien inventer :

**Option 1 : Directe et axée compétences (Recommandée pour ATS)**
> **Ingénieur Backend (4 ans d'expérience)** spécialisé dans la conception d'architectures robustes avec **Python, FastAPI, PostgreSQL et AWS**. Reconnue pour le développement d'API performantes et l'optimisation de bases de données. À la recherche d'un poste de **Senior Backend Engineer** à Casablanca.

**Option 2 : Synthétique et orientée profil**
> **Ingénieur Logiciel Backend** orienté performance et fiabilité, fort de 4 ans d'expérience sur la stack **Python (FastAPI), PostgreSQL et environnement Cloud AWS**. Candidat au poste de **Senior Backend Engineer** pour apporter une expertise technique sur des projets complexes à Casablanca.

---

💡 **Conseil ATS / Impact :** 
Pour rendre ton CV encore plus puissant, ajoute une phrase d'impact chiffrée si tu en as (ex. : *"Optimisation de requêtes PostgreSQL ayant réduit le temps de réponse de X%"* ou *"Déploiement d'API supportant X requêtes/minute"*).


==========================================================================================
# profile_analysis
==========================================================================================


--- groq / openai/gpt-oss-120b (mode=none, 2.6s, 890 tok) ---

{
  "professional_summary": "Senior backend engineer with 5+ years designing and scaling robust high‑throughput services. Proven ability to reduce API latency by 70% and handle millions of transactions monthly using Python, FastAPI, PostgreSQL, Redis, and AWS. Experienced in leading junior developers, implementing code review processes, and migrating monolithic databases to cloud‑native architectures. Strong focus on performance optimization, microservice design, and DevOps automation.",
  "career_objective": "Seeking a senior backend or lead engineering role where I can drive scalable, high‑performance services on cloud platforms.",
  "key_skills": ["Python", "FastAPI", "Django", "PostgreSQL", "Redis", "Docker", "AWS"],
  "strengths": ["Performance optimization", "System design", "Team mentorship"],
  "expertise_areas": ["Backend development", "Cloud architecture"],
  "years_of_experience": 5,
  "seniority_level": "Senior"
}


--- groq / openai/gpt-oss-120b (mode=json_schema, 2.1s, 911 tok) ---

{"professional_summary":"Senior backend engineer with over 6 years of experience designing and delivering high‑throughput services in Python. Proven ability to cut API latency by 70% and handle millions of transactions monthly using FastAPI, PostgreSQL, Redis, and AWS. Leads junior developers through code reviews and promotes best‑practice DevOps pipelines with Docker and Terraform. Strong focus on performance optimization, scalable architecture, and reliable cloud deployments.","career_objective":"Seeking a senior backend engineering role where I can drive high‑performance, cloud‑native services and mentor teams to deliver scalable solutions.","key_skills":["Python","FastAPI","PostgreSQL","Redis","AWS"],"strengths":["Performance optimization","Scalable system design","Team leadership"],"expertise_areas":["Backend development","Cloud architecture"],"years_of_experience":6,"seniority_level":"Senior"}


--- groq / openai/gpt-oss-20b (mode=none, 1.5s, 838 tok) ---

{"professional_summary":"Senior backend engineer with 6 years of experience building high‑throughput billing and authentication services. Expert in Python, PostgreSQL, and FastAPI, delivering scalable APIs that process millions of transactions per month. Proven ability to reduce latency, migrate databases with zero downtime, and mentor junior developers through systematic code reviews. Adept at designing microservices, implementing caching, and deploying on AWS with Docker and Terraform.","career_objective":"Seeking a senior backend engineering role focused on scalable billing and microservices architecture within a high‑growth fintech organization.","key_skills":["Python","PostgreSQL","FastAPI"],"strengths":["Performance optimization","Team leadership","Systematic code review"],"expertise_areas":["Billing systems","Microservices architecture"],"years_of_experience":6,"seniority_level":"Senior"}


--- groq / openai/gpt-oss-20b (mode=json_schema, 2.0s, 1228 tok) ---

{"professional_summary":"Senior Backend Engineer with 6+ years of experience delivering high-volume transaction services, reducing API latency from 800ms to 210ms through Redis caching and query optimization. Expert in Python, FastAPI, and PostgreSQL, with proven ability to migrate monolithic databases to scalable cloud architectures. Leads code reviews and mentors junior developers, ensuring robust, maintainable microservices. Strong focus on performance, reliability, and cloud-native deployment.","career_objective":"Seeking a Senior Backend Engineer role focused on high-volume transaction processing and cloud-native architecture.","key_skills":["Python","FastAPI","PostgreSQL"],"strengths":["Performance optimization","Mentorship & code quality","Cloud-native architecture"],"expertise_areas":["High-volume transaction processing","Microservices architecture"],"years_of_experience":6,"seniority_level":"Senior"}


--- groq / qwen/qwen3.8-27b (mode=none, 0.8s, 253 tok) ---

{
  "professional_summary": "Senior Backend Engineer with 4 years of experience designing high-throughput Python services and optimizing API performance. Proven track record in reducing p95 latency by 73% and managing 2M monthly transactions using FastAPI, PostgreSQL, and Redis. Skilled in leading junior developers, implementing systematic code reviews, and migrating monolithic systems to scalable microservices architectures on AWS.",
  "career_objective": "To leverage deep expertise in Python backend architecture and cloud infrastructure to design scalable, high-performance distributed systems.",
  "key_skills": [
    "Python",
    "FastAPI",
    "PostgreSQL",
    "Redis",
    "Docker",
    "AWS",
    "Microservices",
    "API Optimization"
  ],
  "strengths": [
    "High-performance API optimization",
    "Database migration and scaling",
    "Technical leadership and mentoring"
  ],
  "expertise_areas": [
    "Backend Development",
    "Cloud Infrastructure",
    "System Architecture"
  ],
  "years_of_experience": 4,
  "seniority_level": "Senior"
}


--- groq / qwen/qwen3.8-27b (mode=json_schema, 0.8s, 225 tok) ---

{"career_objective": "To leverage 4+ years of backend engineering expertise in Python and cloud-native architectures to design high-throughput, low-latency distributed systems for a scalable SaaS platform.", "expertise_areas": ["Backend Engineering", "Cloud Infrastructure", "API Optimization", "Database Architecture"], "key_skills": ["Python", "FastAPI", "PostgreSQL", "Redis", "AWS", "Docker", "Microservices", "System Design"], "professional_summary": "Senior Backend Engineer with 4+ years of experience designing high-performance Python services and cloud-native architectures. Proven track record of reducing API latency by 73% and processing 2M monthly transactions using FastAPI, PostgreSQL, and Redis. AWS Certified Solutions Architect specializing in scalable microservices, infrastructure as code, and database optimization. Experienced in leading junior developers and implementing rigorous code review standards to ensure system reliability.", "seniority_level": "Senior", "strengths": ["High-Performance API Optimization", "Cloud-Native System Design", "Technical Leadership & Mentorship"], "years_of_experience": 4}


--- nvidia / nvidia/nemotron-3-super-120b-a12b (mode=none, 11.9s, 1500 tok) ---

We need to produce JSON with given keys. Must be ATS-optimized, no generic phrases. Use info: Mehdi Bennani, education: Engineer in Computer Science 2017-2020. Experience: Senior Backend Engineer at Atlas Data (Mar 2022 - Present). Then Backend Developer at Beta Solutions (Sep 2020 - Feb 2022). Intern at Startup Nova (Feb 2020 - July 2020). So professional experience: from Sep 2020 to present (Mar 2022 - present). Let's calculate years: Sep 2020 to now (assuming current date 2025-09-25). That's about 5 years? Actually Sep 2020 to Sep 2025 = 5 years. But we have only up to present (maybe 2025?). The profile likely as of 2024? Not given. We'll estimate years of experience as integer: from Sep 2020 to present (let's assume 2025-09) => about 5 years. Could also count from Mar 2022 to present (Senior) plus prior experience. Let's compute: Sep 2020 to Feb 2022 = 1.5 years. Mar 2022 to present (let's assume Sep 2025) = 3.5 years. Total ~5 years. So years_of_experience = 5.

Seniority level: He has Senior title at Atlas Data, so Senior.

Professional summary: 3-4 sentences, 60-90 words, ATS-optimized, focusing on technical expertise, specialization, real strengths. No generic phrases. Must highlight key technologies and domains: backend engineering, microservices, API performance optimization, Redis caching, PostgreSQL migration, AWS, Docker, Python, FastAPI, Django, etc.

Career objective: one sentence aligned with target roles (likely Senior Backend Engineer, Cloud-native backend, etc.)

Key skills: list of skills (maybe top 5). Should be from skills list: Python, FastAPI, PostgreSQL, Redis, AWS, Docker, Django, etc. Choose maybe 5-6.

Strengths: list of strengths (like API latency reduction, microservices design, team mentoring, database migration, CI/CD automation). Must be concrete.

Expertise_areas: list of areas (like Backend Engineering, Cloud Infrastructure, Microservices Architecture, Data Optimization).

Now produce JSON.

Check word count for summary: 60-90 words. Let's craft ~75 words.

Sentence 1: Mehdi Bennani is a Senior Backend Engineer with over five years of experience designing high‑performance Python‑based services and migrating legacy systems to cloud‑native architectures. Sentence 2: At Atlas Data he reduced API p95 latency from 800ms to 210ms by implementing Redis caching and query optimization while mentoring two junior developers. Sentence 3: He has built payment and authentication microservices, led a zero‑downtime MySQL‑to‑PostgreSQL migration, and maintains an open‑source recommendation engine with 400+ GitHub stars. Sentence 4: His expertise spans FastAPI, Django, AWS, Docker, Terraform, and CI/CD pipelines using GitHub Actions.

Now count words. Let's count manually.

Sentence1: Mehdi(1) Bennani2 is3 a4 Senior5 Backend6 Engineer7 with8 over9 five10 years11 of12 experience13 designing14 high‑performance15 Python‑based16 services17 and18 migrating19 legacy20 systems21 to22 cloud‑native23 architectures24.

That's 24 words.

Sentence2: At1 Atlas2 Data3 he4 reduced5 API6 p957 latency8 from9 800ms10 to11 210ms12 by13 implementing14 Redis15 caching16 and17 query18 optimization19 while20 mentoring21 two22 junior23 developers24.

24 words.

Sentence3: He1 has2 built3 payment4 and5 authentication6 microservices,7 led8 a9 zero‑downtime10 MySQL‑to‑PostgreSQL11 migration,12 and13 maintains14 an15 open‑source16 recommendation17 engine18 with19 400+20 GitHub21 stars22.

22 words.

Sentence4: His1 expertise2 spans3 FastAPI,4 Django,5 AWS,6 Docker,7 Terraform,8 and9 CI/CD10 pipelines11 using12 GitHub13 Actions14.

14 words.

Total = 24+24+22+14 = 84 words. Good within 60-90.

Now career objective: one sentence. Eg: "Seeking a Senior Backend Engineer role where I can drive scalable, low‑latency services and lead cloud‑native migrations." Count words? Not needed but okay.

Key skills: list of strings. Choose maybe: "Python", "FastAPI", "PostgreSQL", "Redis", "AWS", "Docker". Could also include "Django", "Terraform". We'll pick 6.

Strengths: list of three concrete strengths: "API latency optimization via Redis caching", "Zero‑downtime database migration (MySQL to PostgreSQL)", "Mentoring junior engineers and instituting code review processes". Could also add "Designing payment and authentication microservices". Choose three.

Expertise_areas: list of two areas: "Backend Engineering", "Cloud‑Native Architecture". Or "Microservices Design", "Database Optimization". We'll pick two.

Seniority_level: "Senior".

Years_of_experience: integer 5.

Now produce JSON exactly with keys.

Make sure no trailing commas. Use double quotes.

Let's craft.

{
  "professional_summary": "Mehdi Bennani is a Senior Backend Engineer with over five years of experience designing high‑performance Python‑based services and migrating legacy systems to cloud‑native architectures. At Atlas Data he reduced API p95 latency from 800ms to 210ms by implementing Redis caching and query optimization while mentoring two junior developers. He


--- gemini / gemini-3.5-flash-lite (mode=none, 1.4s, 234 tok) ---

{
    "professional_summary": "Senior Backend Engineer with over 4 years of professional experience specializing in scalable architecture, microservices, and high-performance API development using Python, FastAPI, and Django. Proven track record of optimizing system performance, including reducing p95 API latency by 73% and successfully executing zero-downtime database migrations. Certified AWS Solutions Architect with strong expertise in PostgreSQL, Redis, Docker, and cloud infrastructure.",
    "career_objective": "To secure a Senior Backend Engineer role leveraging cloud-native architectures and distributed systems expertise to scale high-throughput enterprise applications.",
    "key_skills": [
        "Python",
        "FastAPI",
        "Django",
        "PostgreSQL",
        "Redis",
        "Docker",
        "AWS",
        "Microservices"
    ],
    "strengths": [
        "API Latency Optimization",
        "Database Migration & Architecture",
        "Technical Mentorship & Code Review"
    ],
    "expertise_areas": [
        "Backend Development",
        "Cloud Infrastructure & DevOps"
    ],
    "years_of_experience": 4,
    "seniority_level": "Senior"
}


--- gemini / gemini-3.5-flash-lite (mode=json_schema, 1.4s, 232 tok) ---

{
  "professional_summary": "Senior Backend Engineer with over 4 years of experience specializing in Python, FastAPI, and distributed systems architecture. Proven track record of scaling high-throughput applications, optimizing API latency by 73 percent using Redis, and executing zero-downtime database migrations. Certified AWS Solutions Architect with expertise in cloud infrastructure, microservices, and technical leadership.",
  "career_objective": "To secure a Senior Backend Engineer role at a high-growth technology company focused on scalable cloud architecture and high-performance APIs.",
  "key_skills": [
    "Python",
    "FastAPI",
    "Django",
    "PostgreSQL",
    "Redis",
    "Docker",
    "AWS",
    "SQL",
    "Go",
    "RabbitMQ"
  ],
  "strengths": [
    "Performance Optimization",
    "Database Migration",
    "Technical Mentorship"
  ],
  "expertise_areas": [
    "Backend Development",
    "Cloud Architecture",
    "Microservices"
  ],
  "years_of_experience": 4,
  "seniority_level": "Senior"
}


--- gemini / gemini-3.1-flash-lite (mode=none, 1.6s, 225 tok) ---

{
  "professional_summary": "Senior Backend Engineer with over 4 years of experience specializing in high-scale Python architectures and microservices. Proven track record in optimizing API performance, reducing latency by 70%, and managing high-volume transaction systems exceeding 2 million monthly operations. Expert in cloud-native development, database migration, and infrastructure automation using AWS, Docker, and PostgreSQL. Committed to engineering excellence through systematic code reviews and technical mentorship.",
  "career_objective": "Seeking a Senior Backend Engineering role to leverage expertise in distributed systems and cloud architecture to build scalable, high-performance software solutions.",
  "key_skills": [
    "Python",
    "FastAPI",
    "PostgreSQL",
    "Redis",
    "AWS",
    "Docker",
    "Microservices",
    "SQL",
    "Terraform"
  ],
  "strengths": [
    "High-scale system optimization",
    "Database migration and architecture",
    "Technical mentorship and code quality leadership"
  ],
  "expertise_areas": [
    "Backend Development",
    "Cloud Infrastructure",
    "Distributed Systems"
  ],
  "years_of_experience": 4,
  "seniority_level": "Senior"
}


--- gemini / gemini-3.1-flash-lite (mode=json_schema, 1.7s, 273 tok) ---

{
  "professional_summary": "Senior Backend Engineer with over 4 years of experience specializing in high-scale Python microservices and cloud-native architectures. Proven track record in optimizing API performance, managing large-scale transaction systems, and leading technical teams through systematic code reviews. Expert in designing robust database migrations and implementing scalable caching strategies using AWS, PostgreSQL, and Redis. Committed to delivering high-performance, maintainable software solutions that drive business efficiency and reliability.",
  "career_objective": "To leverage extensive backend engineering expertise and cloud architecture skills to build scalable, high-impact distributed systems in a senior technical role.",
  "key_skills": [
    "Python",
    "FastAPI",
    "Django",
    "PostgreSQL",
    "Redis",
    "AWS",
    "Docker",
    "Microservices",
    "SQL",
    "Terraform"
  ],
  "strengths": [
    "API Performance Optimization",
    "Database Migration Strategy",
    "Technical Mentorship",
    "Cloud Infrastructure Management",
    "System Architecture Design"
  ],
  "expertise_areas": [
    "Backend Development",
    "Cloud Engineering",
    "Database Optimization",
    "Distributed Systems"
  ],
  "years_of_experience": 4,
  "seniority_level": "Senior"
}


--- gemini / gemini-3.6-flash (mode=none, 9.0s, 60 tok) ---

 Engineering",
    "Cloud Architecture & DevOps",
    "High-Volume Transaction Systems"
  ],
  "years_of_experience": 4,
  "seniority_level": "Senior"
}
```
Double check word count of summary:
"Senior