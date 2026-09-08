import type { APIRoute } from "astro";
import zombies from "../data/d7_zombies.json";
import weapons from "../data/d7_weapons.json";
import tools from "../data/d7_tools.json";
import mods from "../data/d7_mods.json";
import ammo from "../data/d7_ammo.json";
import armor from "../data/d7_armor.json";

interface Entry {
  t: string;
  u: string;
  k: string;
  i?: string;
}

export const GET: APIRoute = () => {
  const toolsList: Entry[] = [
    { t: "DPS Calculator", u: "/dps-calculator/", k: "Tool" },
    { t: "Zombie HP ranking", u: "/rankings/#zombies", k: "Tool" },
    { t: "Blood Moon radiated elites", u: "/rankings/#radiated", k: "Tool" },
    { t: "Weapon damage rankings", u: "/rankings/#weapons", k: "Tool" },
    { t: "Tool damage rankings", u: "/rankings/#tools", k: "Tool" },
    { t: "All pages A–Z", u: "/search/", k: "Tool" },
  ];
  const items: Entry[] = [
    ...zombies.map((z: any) => ({ t: z.title, u: `/zombies/${z.slug}/`, k: "Zombie", i: z.icon || "" })),
    ...weapons.map((w: any) => ({ t: w.title, u: `/weapons/${w.slug}/`, k: "Weapon", i: w.icon || "" })),
    ...tools.map((t: any) => ({ t: t.title, u: `/tools/${t.slug}/`, k: "Tool item", i: t.icon || "" })),
    ...mods.map((m: any) => ({ t: m.title, u: `/mods/${m.slug}/`, k: "Mod", i: m.icon || "" })),
    ...ammo.map((a: any) => ({ t: a.title, u: `/ammo/${a.slug}/`, k: "Ammo", i: a.icon || "" })),
    ...armor.map((a: any) => ({ t: a.title, u: `/armor/${a.slug}/`, k: "Armor", i: a.icon || "" })),
  ];
  return new Response(JSON.stringify({ tools: toolsList, items }), {
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
};
