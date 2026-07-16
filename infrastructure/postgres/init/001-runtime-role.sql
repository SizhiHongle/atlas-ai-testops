-- 本文件只用于本地开发数据库；生产角色由部署系统管理。
create role atlas_app
  login
  password 'atlas_app'
  nosuperuser
  nocreatedb
  nocreaterole
  nobypassrls
  noinherit;

grant connect on database atlas to atlas_app;

-- Intent Consumer uses a dedicated login; production roles are deployment-managed.
create role atlas_dispatcher
  login
  password 'atlas_dispatcher'
  nosuperuser
  nocreatedb
  nocreaterole
  nobypassrls
  noinherit;

grant connect on database atlas to atlas_dispatcher;
