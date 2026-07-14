-- 本文件只用于本地开发数据库；生产角色由部署系统管理。
create role atlas_app
  login
  password 'atlas_app'
  nosuperuser
  nocreatedb
  nocreaterole
  noinherit;

grant connect on database atlas to atlas_app;
