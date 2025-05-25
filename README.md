# MCQS helper

> a helper api for the MCQS project

### Build and run

```bash
docker build -t mcqs-helper-img .
```

```bash
docker run -d -p 7480:7480 --name mcqs-helper --env-file .env mcqs-helper-img
```

### Email Usage

```bash
curl -X POST http://localhost:7480/feedback \
  -H "Content-Type: application/json" \
  -d '{"to": ["test@example.com"], "html_body": "This is a test feedback"}'
```

### Explain Usage

```bash
curl -X POST http://localhost:7480/explain \
  -H "Content-Type: application/json" \
  -d '{"question":"Your question here", "correct_answer":"The correct answer here"}'
```
