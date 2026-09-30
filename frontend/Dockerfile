# Заглушка на этапе 1 roadmap: контейнер отдаёт статику и проксирует API.
# Настоящая SPA (React + Vite) собирается на этапе 8 и заменяет этот файл.
FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY public /usr/share/nginx/html

EXPOSE 80
