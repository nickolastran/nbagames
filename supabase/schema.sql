-- Run once in the Supabase SQL editor. Email/password auth needs no extra setup;
-- turn "Confirm email" off under Authentication → Providers if you want instant sign-in.

create table if not exists public.games (
  id          bigint generated always as identity primary key,
  user_id     uuid not null default auth.uid() references auth.users on delete cascade,
  date        date not null,
  opp         text not null check (char_length(opp) = 3),
  home        boolean not null default true,
  team_score  int not null default 0 check (team_score between 0 and 300),
  opp_score   int not null default 0 check (opp_score between 0 and 300),
  min         int not null default 0 check (min between 0 and 70),
  pts         int not null default 0 check (pts between 0 and 150),
  reb         int not null default 0 check (reb between 0 and 60),
  ast         int not null default 0 check (ast between 0 and 50),
  stl         int not null default 0 check (stl between 0 and 20),
  blk         int not null default 0 check (blk between 0 and 20),
  fg3m        int not null default 0 check (fg3m between 0 and 30),
  fg3a        int not null default 0 check (fg3a between 0 and 40),
  tov         int not null default 0 check (tov between 0 and 20),
  created_at  timestamptz not null default now(),
  check (fg3a >= fg3m)
);

create index if not exists games_user_date on public.games (user_id, date desc);

alter table public.games enable row level security;

drop policy if exists "own games" on public.games;
create policy "own games" on public.games
  for all to authenticated
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);
