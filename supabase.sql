-- Run once in Supabase: SQL Editor -> New query -> paste -> Run
create table if not exists owned_cards (
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  card_id text not null,
  primary key (user_id, card_id)
);
alter table owned_cards enable row level security;
create policy "own rows read"   on owned_cards for select using (auth.uid() = user_id);
create policy "own rows insert" on owned_cards for insert with check (auth.uid() = user_id);
create policy "own rows delete" on owned_cards for delete using (auth.uid() = user_id);
